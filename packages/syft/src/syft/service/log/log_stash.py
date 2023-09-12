from ...util.telemetry import instrument
from ...serde.serializable import serializable
from ...store.document_store import BaseUIDStoreStash
from ...store.document_store import DocumentStore
from ...store.document_store import PartitionSettings
from .log import SyftLog
from ...node.credentials import SyftVerifyKey
from result import Result

@instrument
@serializable()
class LogStash(BaseUIDStoreStash):
    object_type = SyftLog
    settings: PartitionSettings = PartitionSettings(
        name=SyftLog.__canonical_name__, object_type=SyftLog
    )

    def __init__(self, store: DocumentStore) -> None:
        super().__init__(store=store)