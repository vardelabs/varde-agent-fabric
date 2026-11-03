"""Old import path for the remote worker, kept until gateways stop writing "cluster"."""
from dispatch.remote_worker import RemoteReviewWorker, WorkerResult

ClusterReviewWorker = RemoteReviewWorker

__all__ = ["ClusterReviewWorker", "WorkerResult"]
