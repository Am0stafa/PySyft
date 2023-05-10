# stdlib
from enum import Enum
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Type
from typing import Union

# third party
import pydantic
from pydantic import validator
from result import OkErr

# relative
from ...client.client import SyftClient
from ...node.credentials import SyftSigningKey
from ...node.credentials import SyftVerifyKey
from ...serde.serializable import serializable
from ...serde.serialize import _serialize
from ...service.metadata.node_metadata import NodeMetadata
from ...store.linked_obj import LinkedObject
from ...types.datetime import DateTime
from ...types.syft_object import SYFT_OBJECT_VERSION_1
from ...types.syft_object import SyftObject
from ...types.transforms import TransformContext
from ...types.transforms import generate_id
from ...types.transforms import keep
from ...types.transforms import transform
from ...types.uid import UID
from ..code.user_code import UserCode
from ..request.request import EnumMutation
from ..request.request import Request
from ..request.request import SubmitRequest
from ..request.request import UserCodeStatusChange
from ..request.request_service import RequestService
from ..response import SyftError
from ..response import SyftSuccess
from ..service import TYPE_TO_SERVICE


@serializable()
class NodeIdentity(SyftObject):
    __canonical_name__ = "NodeIdentity"
    __version__ = SYFT_OBJECT_VERSION_1

    id: UID
    verify_key: SyftVerifyKey

    def __hash__(self) -> int:
        return hash(self.id) + hash(self.verify_key)

    __attr_repr_cols__ = ["id", "verify_key"]

    def __repr__(self) -> str:
        verify_key_str = f"{self.verify_key}"
        id_str = f"{self.id}"
        return f"<🔑 {verify_key_str[0:8]} @ 🟢 {id_str[0:8]}>"


@transform(NodeMetadata, NodeIdentity)
def metadata_to_node_identity() -> List[Callable]:
    return [keep(["id", "verify_key"])]


# @serializable()
# class EventTypes(Enum):
#     REQUEST = "REQUEST"
#     USER_CODE = "USER_CODE"


class ProjectEvent(SyftObject):
    __canonical_name__ = "ProjectEvent"
    __version__ = SYFT_OBJECT_VERSION_1

    id: UID = UID()
    project_id: UID
    timestamp: DateTime
    creator_verify_key: SyftVerifyKey
    prev_event_hash: int

    @pydantic.root_validator(pre=True)
    def make_timestamp(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        if "timestamp" not in values or values["timestamp"] is None:
            values["timestamp"] = DateTime.now()
        return values

    event_hash: Optional[int]
    signature: Optional[bytes]

    def __hash__(self) -> int:
        return type(self).calculate_hash(self, self.__hash_keys__)

    @property
    def valid(self) -> Union[SyftSuccess, SyftError]:
        if self.signature is None:
            return SyftError(message="Sign event first")
        try:
            signature = self.signature
            self.signature = None
            signed_bytes = _serialize(self, to_bytes=True)
            self.creator_verify_key.verify_key.verify(signed_bytes, signature)
            self.signature = signature
            return SyftSuccess(message="Event signature is valid")
        except Exception as e:
            return SyftError(message=f"Failed to validate message. {e}")

    def sign(self, signing_key: SyftSigningKey) -> None:
        if signing_key.verify_key != self.creator_verify_key:
            raise Exception("Wrong key")
        self.signature = None
        signed_bytes = _serialize(self, to_bytes=True)
        signed_obj = signing_key.signing_key.sign(signed_bytes)
        self.signature = signed_obj._signature


class ProjectEventAddObject(ProjectEvent):
    __canonical_name__ = "ProjectEventAddObject"
    __version__ = SYFT_OBJECT_VERSION_1


class ProjectEventAddLink(ProjectEvent):
    __canonical_name__ = "ProjectEventAddLink"
    __version__ = SYFT_OBJECT_VERSION_1


@serializable()
class ProjectMessage(ProjectEventAddObject):
    __canonical_name__ = "ProjectMessage"
    __version__ = SYFT_OBJECT_VERSION_1

    message: str

    __hash_keys__ = [
        "id",
        "timestamp",
        "creator_verify_key",
        "prev_event_hash",
        "message",
    ]


class ConsensusModel:
    pass


@serializable()
class DemocraticConsensusModel(ConsensusModel):
    threshold: float = 50

    def __eq__(self, value: object) -> bool:
        if not isinstance(value, DemocraticConsensusModel):
            return False
        return self.threshold == value.threshold

    def __hash__(self) -> int:
        return hash(self.threshold)


@serializable()
class NewProject(SyftObject):
    __canonical_name__ = "NewProject"
    __version__ = SYFT_OBJECT_VERSION_1

    id: Optional[UID]
    name: str
    description: Optional[str]
    shareholders: List[NodeIdentity]
    project_permissions: Set[str]
    state_sync_leader: NodeIdentity
    consensus_model: ConsensusModel
    store: Dict[UID, Dict[UID, SyftObject]] = {}
    permissions: Dict[UID, Dict[UID, Set[str]]] = {}
    events: List[ProjectEvent] = []
    start_hash: int

    __attr_repr_cols__ = ["name", "shareholders", "state_sync_leader"]
    __hash_keys__ = [
        "id",
        "name",
        "description",
        "shareholders",
        "project_permissions",
        "state_sync_leader",
        "consensus_model",
        "store",
        "permissions",
        "events",
    ]

    def __hash__(self) -> int:
        return type(self).calculate_hash(self, self.__hash_keys__)

    def _broadcast_event(
        self, project_event: ProjectEvent
    ) -> Union[SyftSuccess, SyftError]:
        # relative
        from ...client.api import APIRegistry

        api = APIRegistry.api_for(self.state_sync_leader.id)
        if api is None:
            return SyftError(
                message=f"You must login to {self.state_sync_leader.name}-{self.state_sync_leader.id}"
            )
        return api.services.newproject.broadcast_event(project_event)

    def add_event(self, event: ProjectEvent) -> Union[SyftSuccess, SyftError]:
        # event = ProjectEvent(
        #     event_type=EventTypes.REQUEST,
        #     event_data=obj,
        #     user_verify_key=obj.requesting_user_verify_key,
        #     project_id=self.id,
        # )
        self.events.append(event)
        return self._broadcast_event(event)


@serializable()
class NewProjectSubmit(SyftObject):
    __canonical_name__ = "NewProjectSubmit"
    __version__ = SYFT_OBJECT_VERSION_1

    @validator("shareholders", pre=True)
    def make_shareholders(cls, objs: List[SyftClient]) -> List[NodeIdentity]:
        shareholders = []
        for obj in objs:
            if isinstance(obj, NodeIdentity):
                shareholders.append(obj)
            elif isinstance(obj, SyftClient):
                metadata = obj.metadata.to(NodeMetadata)
                node_identity = metadata.to(NodeIdentity)
                shareholders.append(node_identity)
            else:
                raise Exception(
                    f"Shareholders should be either SyftClient or NodeIdentity received: {type(obj)}"
                )
        return shareholders

    id: Optional[UID]
    name: str
    description: Optional[str]
    shareholders: List[NodeIdentity]
    project_permissions: Set[str] = set()
    state_sync_leader: Optional[NodeIdentity]
    consensus_model: ConsensusModel

    def start(self) -> NewProject:
        # Creating a new unique UID to be used by all shareholders
        project_id = UID()
        projects = []
        for shareholder in self.shareholders:
            # relative
            from ...client.api import APIRegistry

            api = APIRegistry.api_for(shareholder.id)
            if api is None:
                raise Exception(f"You must login to {shareholder.id}")
            result = api.services.newproject.create_project(
                project=self, project_id=project_id
            )
            if isinstance(result, SyftError):
                return result
            else:
                projects.append(result)

        # as we currently assume that the first shareholder is the leader.
        return projects

    __attr_repr_cols__ = ["name"]


def add_shareholders_as_owners(shareholders: List[SyftVerifyKey]) -> Set[str]:
    keys = set()
    for shareholder in shareholders:
        owner_key = f"OWNER_{shareholder.verify_key}"
        keys.add(owner_key)
    return keys


def elect_leader(context: TransformContext) -> TransformContext:
    if len(context.output["shareholders"]) == 0:
        raise Exception("Project's require at least one shareholder")

    # leader_key: Optional[SyftVerifyKey] = None

    # shareholders_verify_key = [
    #     shareholder.verify_key for shareholder in context.output["shareholders"]
    # ]

    # TODO: implement consensus model for selecting a leader
    # Assume by default that the first shareholder is the leader
    # leader_key = shareholders_verify_key[0]

    # if context.node.verify_key == leader_key:
    #     # get NodePeer for self
    #     peer = context.node.metadata.to(NodePeer)
    # else:
    #     peer = context.node.get_service("networkservice").stash.get_for_verify_key(leader_key)
    #     if peer.is_err():
    #         raise Exception(f"Leader is unknown peer. {leader_key}")
    #     peer = peer.ok()

    # TODO: implement consensus model for selecting a leader
    # Assume by default that the first shareholder is the leader
    context.output["state_sync_leader"] = context.output["shareholders"][0]

    return context


def check_permissions(context: TransformContext) -> TransformContext:
    if len(context.output["shareholders"]) > 1:
        # more than 1 node
        pass

    # check at least one owner
    if len(context.output["project_permissions"]) == 0:
        project_permissions = context.output["project_permissions"]
        project_permissions = project_permissions.union(
            add_shareholders_as_owners(context.output["shareholders"])
        )
        context.output["project_permissions"] = project_permissions

    return context


def calculate_final_hash(context: TransformContext) -> TransformContext:
    context.output["store"] = {}
    context.output["permissions"] = {}
    context.output["events"] = []

    start_hash = NewProject.calculate_hash(context.output, NewProject.__hash_keys__)
    context.output["start_hash"] = start_hash
    return context


@transform(NewProjectSubmit, NewProject)
def new_projectsubmit_to_project() -> List[Callable]:
    return [elect_leader, check_permissions, calculate_final_hash]


@serializable()
class Project(SyftObject):
    __canonical_name__ = "Project"
    __version__ = SYFT_OBJECT_VERSION_1

    name: str
    description: str
    user_verify_key: SyftVerifyKey
    requests: Optional[List[Request]]

    __attr_searchable__ = [
        "user_verify_key",
        "name",
    ]
    __attr_unique__ = ["name"]

    __attr_repr_cols__ = ["requests"]


@serializable()
class ObjectPermissionChange(SyftObject):
    __canonical_name__ = "PermissionChange"
    __version__ = SYFT_OBJECT_VERSION_1

    object_uid: UID
    permission: Enum
    object_type: Type[SyftObject]


@serializable()
class ProjectSubmit(SyftObject):
    __canonical_name__ = "ProjectSubmit"
    __version__ = SYFT_OBJECT_VERSION_1

    id: Optional[UID]
    name: str
    description: Optional[str]
    changes: List[ObjectPermissionChange] = []

    __attr_repr_cols__ = ["name", "changes"]

    def set_description(self, description: str, msg: Optional[str] = None) -> None:
        self.description = description

    def add_request(self, obj: SyftObject, permission: Enum) -> None:
        change = ObjectPermissionChange(
            object_uid=obj.id, object_type=type(obj), permission=permission
        )
        self.changes.append(change)


def submit_changes(context: TransformContext) -> TransformContext:
    changes = context.output.pop("changes", [])
    if changes:
        mutations = []
        for change in changes:
            service_type = TYPE_TO_SERVICE[change.object_type]
            linked_obj = LinkedObject(
                node_uid=context.node.id,
                service_type=service_type,
                object_type=change.object_type,
                object_uid=change.object_uid,
            )

            if change.object_type == UserCode:
                mutation = UserCodeStatusChange(
                    value=change.permission, linked_obj=linked_obj
                )
            else:
                mutation = EnumMutation.from_obj(
                    linked_obj=linked_obj, attr_name="status", value=change.permission
                )

            mutations.append(mutation)
        submit_request = SubmitRequest(changes=mutations)
        request_submit_method = context.node.get_service_method(RequestService.submit)
        result = request_submit_method(
            context=context, request=submit_request, send_message=False
        )

        if isinstance(result, SyftError):
            return result
        if isinstance(result, OkErr):
            result = result.ok()
        context.output["requests"] = [result]
    context.output["user_verify_key"] = context.credentials
    return context


@transform(ProjectSubmit, Project)
def projectsubmit_to_project() -> List[Callable]:
    return [generate_id, submit_changes]
