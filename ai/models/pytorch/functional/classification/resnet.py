'''
    Generic ResNet model for image classification.

    2019 Benjamin Kellenberger
'''

import torch
import torch.nn as nn
from torchvision.models import resnet


class ResNet(nn.Module):
    in_features = {
        'resnet18': 512,
        'resnet34': 512,
        'resnet50': 2048,
        'resnet101': 2048,
        'resnet152': 2048
    }

    def __init__(self, labelclassMap, featureExtractor='resnet50', pretrained=True):
        super(ResNet, self).__init__()

        self.labelclassMap = labelclassMap
        self.featureExtractor = featureExtractor
        self.pretrained = pretrained

        # create actual model
        if isinstance(featureExtractor, str):
            featureExtractor = getattr(resnet, featureExtractor)
        self.fe = featureExtractor(pretrained)
        self.fe = nn.Sequential(*list(self.fe.children())[:-1])

        self.classifier = nn.Linear(in_features=self.in_features[featureExtractor.__name__],
                                    out_features=len(labelclassMap.keys()), bias=True)
    

    def getStateDict(self):
        stateDict = {
            'model_state': self.state_dict(),
            'labelclassMap': self.labelclassMap,
            'featureExtractor': self.featureExtractor,
            'pretrained': self.pretrained
        }
        return stateDict

    
    def updateModel(self, labelClasses, addMissing=False, removeObsolete=False):
        '''
            Receives a new dict of label classes ("labelClasses") and compares
            it with the current one. If "labelClasses" contains new label classes
            that are not present in the current, and if "addMissing" is True, new
            neurons are added for each class. Likewise, if the current model predicts
            label classes that are not present in the new "labelClasses", and if
            "removeObsolete" is True, those neurons are being removed.
        '''
        if not addMissing or not removeObsolete:
            return
        
        classes_current = set([lc for lc in self.labelclassMap.keys()])
        classes_new = set([lc for lc in labelClasses.keys()])
        classes_missing = classes_new.difference(classes_current)
        classes_obsolete = classes_current.difference(classes_new)

        # add new neurons
        if addMissing and len(classes_missing):
            weights = self.classifier.weight
            biases = self.classifier.bias

            # find set of sum of weights and biases with minimal difference to zero
            massValues = []
            for idx in range(0, weights.size(0), self.numAnchors):
                wbSum = torch.sum(torch.abs(weights[idx:(idx+self.numAnchors),...])) + \
                        torch.sum(torch.abs(biases[idx:(idx+self.numAnchors)]))
                massValues.append(wbSum.unsqueeze(0))
            massValues = torch.cat(massValues, 0)
            
            smallest = torch.argmin(massValues)

            newWeights = weights[smallest:(smallest+1), ...]
            newBiases = biases[smallest:(smallest+1)]

            for classname in classes_missing:
                # add a tiny bit of noise for better specialization capabilities (TODO: assess long-term effect of that...)
                noiseW = 0.01 * (0.5 - torch.rand_like(newWeights))
                noiseB = 0.01 * (0.5 - torch.rand_like(newBiases))
                weights = torch.cat((weights, newWeights.clone() + noiseW), 0)
                biases = torch.cat((biases, newBiases.clone() + noiseB), 0)

                # update labelclass map
                self.labelclassMap[classname] = len(self.labelclassMap)
        
            # apply updated weights and biases
            self.classifier.weight = nn.Parameter(weights)
            self.classifier.bias = nn.Parameter(biases)

            print(f'Neurons for {len(classes_missing)} new label classes added to ResNet model.')

        # remove superfluous neurons
        if removeObsolete and len(classes_obsolete):
            weights = self.classifier.weight
            biases = self.classifier.bias

            for classname in classes_obsolete:
                classIdx = self.labelclassMap[classname]

                # remove neurons: slice tensors
                weights = torch.cat((weights[0:classIdx,...], weights[(classIdx+1):,...]), 0)
                biases = torch.cat((biases[0:classIdx], biases[(classIdx+1):]), 0)

                # shift down indices of labelclass map
                del self.labelclassMap[classname]
                for key in self.labelclassMap.keys():
                    if self.labelclassMap[key] > classIdx:
                        self.labelclassMap[key] -= 1

            # apply updated weights and biases
            self.classifier.weight = nn.Parameter(weights)
            self.classifier.bias = nn.Parameter(biases)

            print(f'Neurons of {len(classes_obsolete)} obsolete label classes removed from RetinaNet model.')


    @staticmethod
    def loadFromStateDict(stateDict):
        # parse args
        labelclassMap = stateDict['labelclassMap']
        featureExtractor = (stateDict['featureExtractor'] if 'featureExtractor' in stateDict else 'resnet50')
        pretrained = (stateDict['pretrained'] if 'pretrained' in stateDict else True)
        state = (stateDict['model_state'] if 'model_state' in stateDict else None)

        # return model
        model = ResNet(labelclassMap, featureExtractor, pretrained)
        if state is not None:
            model.load_state_dict(state)
        return model

    
    @staticmethod
    def averageStateDicts(stateDicts):
        model = ResNet.loadFromStateDict(stateDicts[0])
        pars = dict(model.named_parameters())
        for key in pars:
            pars[key] = pars[key].detach().cpu()
        for s in range(1,len(stateDicts)):
            nextModel = ResNet.loadFromStateDict(stateDicts[s])
            state = dict(nextModel.named_parameters())
            for key in state:
                pars[key] += state[key].detach().cpu()
        finalState = stateDicts[-1]
        for key in pars:
            finalState['model_state'][key] = pars[key] / (len(stateDicts))

        return finalState

    
    @staticmethod
    def averageEpochs(statePaths):
        if isinstance(statePaths, str):
            statePaths = [statePaths]
        model = ResNet.loadFromStateDict(torch.load(statePaths[0], map_location=lambda storage, loc: storage))
        if len(statePaths) == 1:
            return model
        
        pars = dict(model.named_parameters())
        for key in pars:
            pars[key] = pars[key].detach().cpu()
        for s in statePaths[1:]:
            model = ResNet.loadFromStateDict(torch.load(s, map_location=lambda storage, loc: storage))
            state = dict(model.named_parameters())
            for key in state:
                pars[key] += state[key]
        
        finalState = torch.load(statePaths[-1], map_location=lambda storage, loc: storage)
        for key in pars:
            finalState['model_state'][key] = pars[key] / (len(statePaths))
        
        model = ResNet.loadFromStateDict(finalState)
        return model


    def getParameters(self,freezeFE=False):
        headerParams = list(self.classifier.parameters())
        if freezeFE:
            return headerParams
        else:
            return list(self.fe.parameters()) + headerParams

    
    def forward(self, x, isFeatureVector=False):
        bs = x.size(0)
        if isFeatureVector:
            yhat = self.classifier(x.view(bs, -1))
        else:
            fVec = self.fe(x)
            yhat = self.classifier(fVec.view(bs, -1))
        return yhat