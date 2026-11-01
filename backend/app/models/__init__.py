"""SQLAlchemy models.

Importing this package registers every mapper on `Base.metadata`, which Alembic
autogenerate and `create_all` both rely on.
"""
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.cbs import (
    CbsAccount,
    CbsAuditEvent,
    CbsCustomer,
    CbsKycRecord,
    CbsMakerCheckerTask,
    CbsTransaction,
)
from app.models.mapping import BusinessRule, ImpactMapping
from app.models.ops import AgentActivityLog, DiscoveryRun, Notification, User
from app.models.regulatory import (
    RegulatoryChange,
    RegulatoryClause,
    RegulatoryDocument,
    RegulatorySource,
)
from app.models.testing import (
    ExecutionEvidence,
    TestApproval,
    TestCase,
    TestExecution,
    TestExecutionRun,
)

__all__ = [
    "Base",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    # ops
    "User",
    "DiscoveryRun",
    "AgentActivityLog",
    "Notification",
    # regulatory
    "RegulatorySource",
    "RegulatoryDocument",
    "RegulatoryClause",
    "RegulatoryChange",
    # mapping
    "BusinessRule",
    "ImpactMapping",
    # testing
    "TestCase",
    "TestApproval",
    "TestExecutionRun",
    "TestExecution",
    "ExecutionEvidence",
    # cbs simulator
    "CbsCustomer",
    "CbsKycRecord",
    "CbsAccount",
    "CbsTransaction",
    "CbsMakerCheckerTask",
    "CbsAuditEvent",
]
