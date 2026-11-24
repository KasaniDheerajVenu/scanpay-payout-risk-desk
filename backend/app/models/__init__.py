"""Import the models so all eleven application tables register on the shared metadata."""

from app.database.base import Base
from app.models.dataset_batch import DatasetBatch
from app.models.evaluation_run import EvaluationRun
from app.models.fraud_cluster import FraudCluster
from app.models.model_version import ModelVersion
from app.models.payout import Payout
from app.models.payout_decision import PayoutDecision
from app.models.payout_decision_history import PayoutDecisionHistory
from app.models.team import Team
from app.models.transaction import Transaction
from app.models.uploaded_file import UploadedFile
from app.models.user import User

__all__ = [
    "Base",
    "DatasetBatch",
    "EvaluationRun",
    "FraudCluster",
    "ModelVersion",
    "Payout",
    "PayoutDecision",
    "PayoutDecisionHistory",
    "Team",
    "Transaction",
    "UploadedFile",
    "User",
]
