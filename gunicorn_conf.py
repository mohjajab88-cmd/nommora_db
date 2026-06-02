# gunicorn_conf.py
import multiprocessing

bind = "0.0.0.0:8000"
workers = 4  # nombre de processus parallèles (ajustez selon votre CPU, 2 à 4 suffisent)
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
max_requests = 1000
max_requests_jitter = 50
timeout = 30
graceful_timeout = 30