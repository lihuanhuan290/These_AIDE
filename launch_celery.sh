# Launches a Celery consumer on the current machine.
# Requires pwd to be the root of the project and the correct Python
# env to be loaded.
#
# 2019-20 Benjamin Kellenberger

# Celery
celery -A celery_worker worker -Q aide_broadcast,$AIDE_MODULES --hostname multibranch@%h