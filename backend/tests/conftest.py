import os

# Pytest lokal memakai .env (CELERY_TASK_ALWAYS_EAGER=false, sering tanpa Redis) sehingga
# .delay() jatuh ke fallback in-process dan menjalankan tracing sungguhan di dalam request
# upload — test upload jadi ikut menunggu pipeline. CI sudah aman karena eager=true.
os.environ.setdefault("AUTO_TRACE_ON_UPLOAD", "false")
