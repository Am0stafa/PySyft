# stdlib
from enum import Enum
import hashlib
from typing import Any
from typing import Callable
from typing import List
from typing import Optional
from typing import Type
from typing import Union

# third party
from result import Err
from result import Ok
from result import Result
from typing_extensions import Self

# relative
from ....core.node.common.node_table.syft_object import SYFT_OBJECT_VERSION_1
from ....core.node.common.node_table.syft_object import SyftBaseObject
from ....core.node.common.node_table.syft_object import SyftObject
from ...common.serde import _serialize
from ...common.serde.serializable import serializable
from ...common.uid import UID
from .action_object import ActionObject
from .action_service import ActionService
from .action_store import ActionObjectPermission
from .action_store import ActionPermission
from .api import APIRegistry
from .context import AuthedServiceContext
from .credentials import SyftVerifyKey
from .datetime import DateTime
from .linked_obj import LinkedObject
from .node import NewNode
from .response import SyftError
from .response import SyftSuccess
from .transforms import TransformContext
from .transforms import add_node_uid_for_key
from .transforms import generate_id
from .transforms import transform
from .user_code import UserCode
from .user_code import UserCodeStatus


@serializable(recursive_serde=True)
class RequestStatus(Enum):
    PENDING = 0
    REJECTED = 1
    APPROVED = 2


class ChangeContext(SyftBaseObject):
    node: Optional[NewNode] = None
    approving_user_credentials: Optional[SyftVerifyKey]
    requesting_user_credentials: Optional[SyftVerifyKey]

    @staticmethod
    def from_service(context: AuthedServiceContext) -> Self:
        return ChangeContext(
            node=context.node, approving_user_credentials=context.credentials
        )


@serializable(recursive_serde=True)
class Change(SyftObject):
    __canonical_name__ = "Change"
    __version__ = SYFT_OBJECT_VERSION_1

    linked_obj: Optional[LinkedObject]

    def is_type(self, type_: type) -> bool:
        return self.linked_obj and type_ == self.linked_obj.object_type


@serializable(recursive_serde=True)
class ActionStoreChange(Change):
    __canonical_name__ = "ActionStoreChange"
    __version__ = SYFT_OBJECT_VERSION_1

    linked_obj: LinkedObject
    apply_permission_type: ActionPermission

    __attr_repr_cols__ = ["linked_obj", "apply_permission_type"]

    def _run(
        self, context: ChangeContext, apply: bool
    ) -> Result[SyftSuccess, SyftError]:
        try:
            action_service = context.node.get_service(ActionService)
            action_store = action_service.store
            owner_permission = ActionObjectPermission(
                uid=self.linked_obj.object_uid,
                credentials=context.approving_user_credentials,
                permission=self.apply_permission_type,
            )
            if action_store.has_permission(permission=owner_permission):
                requesting_permission = ActionObjectPermission(
                    uid=self.linked_obj.object_uid,
                    credentials=context.requesting_user_credentials,
                    permission=self.apply_permission_type,
                )
                if apply:
                    action_store.add_permission(requesting_permission)
                else:
                    action_store.remove_permission(requesting_permission)
            else:
                return Err(
                    SyftError(
                        message=f"No permission for approving_user_credentials {context.approving_user_credentials}"
                    )
                )
            return Ok(SyftSuccess(message=f"{type(self)} Success"))
        except Exception as e:
            print(f"failed to apply {type(self)}")
            return Err(SyftError(message=str(e)))

    def apply(self, context: ChangeContext) -> Result[SyftSuccess, SyftError]:
        return self._run(context=context, apply=True)

    def revert(self, context: ChangeContext) -> Result[SyftSuccess, SyftError]:
        return self._run(context=context, apply=False)


@serializable(recursive_serde=True)
class Request(SyftObject):
    __canonical_name__ = "Request"
    __version__ = SYFT_OBJECT_VERSION_1

    requesting_user_verify_key: SyftVerifyKey
    approving_user_verify_key: Optional[SyftVerifyKey]
    request_time: DateTime
    approval_time: Optional[DateTime]
    status: RequestStatus = RequestStatus.PENDING
    node_uid: UID
    request_hash: str
    changes: List[Change]

    __attr_searchable__ = [
        "requesting_user_verify_key",
        "approving_user_verify_key",
        "status",
    ]
    __attr_unique__ = ["request_hash"]
    __attr_repr_cols__ = ["request_time", "status", "changes"]

    def approve(self):
        api = APIRegistry.api_for(self.node_uid)
        return api.services.request.apply(self.id)

    def apply(self, context: AuthedServiceContext) -> Result[SyftSuccess, SyftError]:
        change_context = ChangeContext.from_service(context)
        change_context.requesting_user_credentials = self.requesting_user_verify_key
        for change in self.changes:
            result = change.apply(context=change_context)
            if result.is_err():
                return result
        return Ok(SyftSuccess(message=f"Request {self.id} changes applied"))

    def revert(self, context: AuthedServiceContext) -> Result[SyftSuccess, SyftError]:
        change_context = ChangeContext.from_service(context)
        change_context.requesting_user_credentials = self.requesting_user_verify_key
        for change in self.changes:
            result = change.revert(context=change_context)
            if result.is_err():
                return result
        return Ok(SyftSuccess(message=f"Request {self.id} changes reverted"))

    def accept_by_depositing_result(self, result: Any):
        if len(self.changes) != 1:
            raise Exception(
                f"accept_by_depositing_result can only be run on {UserCode} Requests"
            )

        change = self.changes[0]
        if not change.is_type(UserCode):
            raise Exception(
                f"accept_by_depositing_result can only be run on {UserCode} not "
                f"{change.linked_obj.object_type}"
            )
        if not change.enum_type == UserCodeStatus:
            raise Exception(
                f"accept_by_depositing_result can only be run on {UserCodeStatus} not "
                f"{change.enum_type}"
            )

        api = APIRegistry.api_for(self.node_uid)
        if not api:
            raise Exception(f"Login to {self.node_uid} first.")

        action_object = ActionObject.from_obj(result)
        result = api.services.action.save(action_object)
        if not result:
            return result

        code = change.linked_obj.resolve
        state = code.output_policy_state
        ctx = AuthedServiceContext(credentials=api.signing_key.verify_key)
        state.update_state(outputs=action_object.id, context=ctx)
        policy_state_mutation = ObjectMutation(
            linked_obj=change.linked_obj,
            attr_name="output_policy_state",
            match_type=True,
            value=state,
        )

        action_object_link = LinkedObject.from_obj(
            action_object, node_uid=self.node_uid
        )

        permission_change = ActionStoreChange(
            linked_obj=action_object_link, apply_permission_type=ActionPermission.READ
        )

        submit_request = SubmitRequest(
            changes=[policy_state_mutation, permission_change],
            requesting_user_verify_key=self.requesting_user_verify_key,
        )

        new_request = api.services.request.submit(submit_request)
        if not new_request:
            return new_request
        new_request_result = api.services.request.apply(new_request.id)
        if not new_request_result:
            return new_request_result
        result = api.services.request.apply(self.id)
        return result


@serializable(recursive_serde=True)
class SubmitRequest(SyftObject):
    __canonical_name__ = "SubmitRequest"
    __version__ = SYFT_OBJECT_VERSION_1

    changes: List[Change]
    requesting_user_verify_key: Optional[SyftVerifyKey]


def hash_changes(context: TransformContext) -> TransformContext:
    request_time = context.output["request_time"]
    key = context.output["requesting_user_verify_key"]
    changes = context.output["changes"]

    time_hash = hashlib.sha256(
        _serialize(request_time.utc_timestamp, to_bytes=True)
    ).digest()
    key_hash = hashlib.sha256(bytes(key.verify_key)).digest()
    changes_hash = hashlib.sha256(_serialize(changes, to_bytes=True)).digest()
    final_hash = hashlib.sha256((time_hash + key_hash + changes_hash)).hexdigest()

    context.output["request_hash"] = final_hash
    return context


def add_request_time(context: TransformContext) -> TransformContext:
    context.output["request_time"] = DateTime.now()
    return context


def check_requesting_user_verify_key(context: TransformContext) -> TransformContext:
    if context.obj.requesting_user_verify_key and context.node.is_root(
        context.credentials
    ):
        context.output[
            "requesting_user_verify_key"
        ] = context.obj.requesting_user_verify_key
    else:
        context.output["requesting_user_verify_key"] = context.credentials
    return context


@transform(SubmitRequest, Request)
def submit_request_to_request() -> List[Callable]:
    return [
        generate_id,
        add_node_uid_for_key("node_uid"),
        add_request_time,
        check_requesting_user_verify_key,
        hash_changes,
    ]


@serializable(recursive_serde=True)
class ObjectMutation(Change):
    __canonical_name__ = "ObjectMutation"
    __version__ = SYFT_OBJECT_VERSION_1

    linked_obj: Optional[LinkedObject]
    attr_name: str
    value: Optional[Any]
    match_type: bool

    __attr_repr_cols__ = ["linked_obj", "attr_name"]

    def mutate(self, obj: Any) -> Any:
        setattr(obj, self.attr_name, self.value)
        return obj

    def _run(
        self, context: ChangeContext, apply: bool
    ) -> Result[SyftSuccess, SyftError]:
        try:
            obj = self.linked_obj.resolve_with_context(context)
            if obj.is_err():
                return SyftError(message=obj.err())
            obj = obj.ok()
            if apply:
                obj = self.mutate(obj)
                self.linked_obj.update_with_context(context, obj)
            else:
                raise NotImplementedError
            return Ok(SyftSuccess(message=f"{type(self)} Success"))
        except Exception as e:
            print(f"failed to apply {type(self)}. {e}")
            return Err(SyftError(message=e))

    def apply(self, context: ChangeContext) -> Result[SyftSuccess, SyftError]:
        return self._run(context=context, apply=True)


def type_for_field(object_type: type, attr_name: str) -> Optional[type]:
    field_type = None
    try:
        field_type = object_type.__dict__["__annotations__"][attr_name]
    except Exception:  # nosec
        try:
            field_type = object_type.__fields__.get(attr_name, None).type_
        except Exception:  # nosec
            pass
    return field_type


@serializable(recursive_serde=True)
class EnumMutation(ObjectMutation):
    __canonical_name__ = "EnumMutation"
    __version__ = SYFT_OBJECT_VERSION_1

    enum_type: Type[Enum]
    value: Optional[Enum]
    match_type: bool = True

    __attr_repr_cols__ = ["linked_obj", "attr_name", "value"]

    def __init__(
        self,
        attr_name: str,
        enum_type: Type[Enum],
        match_type: bool = True,
        linked_obj: Optional[LinkedObject] = None,
        value: Optional[Enum] = None,
        id: Optional[UID] = None,
    ) -> None:
        if id is None:
            id = UID()

        super().__init__(
            id=id,
            linked_obj=linked_obj,
            attr_name=attr_name,
            value=value,
            enum_type=enum_type,
            match_type=match_type,
        )

    @property
    def valid(self) -> Union[SyftSuccess, SyftError]:
        if self.match_type and not isinstance(self.value, self.enum_type):
            return SyftError(
                message=f"{type(self.value)} must be of type: {self.enum_type}"
            )
        return SyftSuccess(message=f"{type(self)} valid")

    @staticmethod
    def from_obj(
        linked_obj: LinkedObject, attr_name: str, value: Optional[Enum] = None
    ) -> Self:
        enum_type = type_for_field(linked_obj.object_type, attr_name)
        return EnumMutation(
            linked_obj=linked_obj,
            attr_name=attr_name,
            enum_type=enum_type,
            value=value,
            match_type=True,
        )

    def _run(
        self, context: ChangeContext, apply: bool
    ) -> Result[SyftSuccess, SyftError]:
        try:
            valid = self.valid
            if not valid:
                return Err(valid)
            obj = self.linked_obj.resolve_with_context(context)
            if obj.is_err():
                return SyftError(message=obj.err())
            obj = obj.ok()
            if apply:
                obj = self.mutate(obj)
                self.linked_obj.update_with_context(context, obj)
            else:
                raise NotImplementedError
            return Ok(SyftSuccess(message=f"{type(self)} Success"))
        except Exception as e:
            print(f"failed to apply {type(self)}. {e}")
            return Err(SyftError(message=e))

    def apply(self, context: ChangeContext) -> Result[SyftSuccess, SyftError]:
        return self._run(context=context, apply=True)

    def revert(self, context: ChangeContext) -> Result[SyftSuccess, SyftError]:
        return self._run(context=context, apply=False)

    @property
    def link(self) -> Optional[SyftObject]:
        if self.linked_obj:
            return self.linked_obj.resolve
        return None