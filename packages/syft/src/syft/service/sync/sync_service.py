# stdlib
from collections import defaultdict
from typing import Any
from typing import Dict
from typing import List
from typing import Set
from typing import Union
from typing import cast

# third party
from result import Result

# relative
from ...abstract_node import AbstractNode
from ...client.api import NodeIdentity
from ...node.credentials import SyftVerifyKey
from ...serde.serializable import serializable
from ...store.document_store import BaseStash
from ...store.document_store import DocumentStore
from ...store.linked_obj import LinkedObject
from ...types.syft_object import SyftObject
from ...types.uid import UID
from ...util.telemetry import instrument
from ..action.action_object import ActionObject
from ..action.action_permissions import ActionObjectPermission
from ..action.action_permissions import ActionPermission
from ..code.user_code import UserCodeStatusCollection
from ..context import AuthedServiceContext
from ..job.job_stash import Job
from ..output.output_service import ExecutionOutput
from ..response import SyftError
from ..response import SyftSuccess
from ..service import AbstractService
from ..service import service_method
from ..user.user_roles import ADMIN_ROLE_LEVEL
from .sync_stash import SyncStash
from .sync_state import SyncState


@instrument
@serializable()
class SyncService(AbstractService):
    store: DocumentStore
    stash: SyncStash

    def __init__(self, store: DocumentStore):
        self.store = store
        self.stash = SyncStash(store=store)

    def add_actionobject_read_permissions(
        self,
        context: AuthedServiceContext,
        action_object: ActionObject,
        permissions_other: List[str],
    ) -> None:
        read_permissions = [x for x in permissions_other if "READ" in x]

        _id = action_object.id.id
        blob_id = action_object.syft_blob_storage_entry_id

        store_to = context.node.get_service("actionservice").store  # type: ignore
        store_to_blob = context.node.get_service("blobstorageservice").stash.partition  # type: ignore

        for read_permission in read_permissions:
            creds, perm_str = read_permission.split("_")
            perm = ActionPermission[perm_str]
            permission = ActionObjectPermission(
                uid=_id, permission=perm, credentials=SyftVerifyKey(creds)
            )
            store_to.add_permission(permission)

            permission_blob = ActionObjectPermission(
                uid=blob_id, permission=perm, credentials=SyftVerifyKey(creds)
            )
            store_to_blob.add_permission(permission_blob)

    def set_obj_ids(self, context: AuthedServiceContext, x: Any) -> None:
        if hasattr(x, "__dict__") and isinstance(x, SyftObject):
            for val in x.__dict__.values():
                if isinstance(val, (list, tuple)):
                    for v in val:
                        self.set_obj_ids(context, v)
                elif isinstance(val, dict):
                    for v in val.values():
                        self.set_obj_ids(context, v)
                else:
                    self.set_obj_ids(context, val)
            x.syft_node_location = context.node.id  # type: ignore
            x.syft_client_verify_key = context.credentials
            if hasattr(x, "node_uid"):
                x.node_uid = context.node.id  # type: ignore

    def transform_item(
        self, context: AuthedServiceContext, item: SyftObject
    ) -> SyftObject:
        if isinstance(item, UserCodeStatusCollection):
            identity = NodeIdentity.from_node(context.node)
            res = {}
            for key in item.status_dict.keys():
                # todo, check if they are actually only two nodes
                res[identity] = item.status_dict[key]
            item.status_dict = res

        self.set_obj_ids(context, item)
        return item

    def get_stash_for_item(
        self, context: AuthedServiceContext, item: SyftObject
    ) -> BaseStash:
        services = list(context.node.service_path_map.values())  # type: ignore

        all_stashes = {}
        for serv in services:
            if (_stash := getattr(serv, "stash", None)) is not None:
                all_stashes[_stash.object_type] = _stash

        stash = all_stashes.get(type(item), None)
        return stash

    def add_permissions_for_item(
        self,
        context: AuthedServiceContext,
        item: SyftObject,
        permissions_other: Set[ActionObjectPermission],
    ) -> None:
        if isinstance(item, Job) and context.node.node_side_type.value == "low":  # type: ignore
            _id = item.id
            read_permissions = [x for x in permissions_other if "READ" in x]  # type: ignore
            job_store = context.node.get_service("jobservice").stash.partition  # type: ignore
            for read_permission in read_permissions:
                creds, perm_str = read_permission.split("_")
                perm = ActionPermission[perm_str]
                permission = ActionObjectPermission(
                    uid=_id, permission=perm, credentials=SyftVerifyKey(creds)
                )
                job_store.add_permission(permission)

    def set_object(
        self, context: AuthedServiceContext, item: SyftObject
    ) -> Result[SyftObject, str]:
        stash = self.get_stash_for_item(context, item)
        creds = context.credentials

        exists = stash.get_by_uid(context.credentials, item.id).ok() is not None
        if exists:
            res = stash.update(creds, item)
        else:
            #                 res = stash.delete_by_uid(node.python_node.verify_key, item.id)
            res = stash.set(creds, item)
        return res

    @service_method(
        path="sync.sync_items",
        name="sync_items",
        roles=ADMIN_ROLE_LEVEL,
    )
    def sync_items(
        self,
        context: AuthedServiceContext,
        items: List[Union[ActionObject, SyftObject]],
        permissions: Dict[UID, Set[str]],
    ) -> Union[SyftSuccess, SyftError]:
        permissions = defaultdict(list, permissions)
        for item in items:
            other_node_permissions = permissions[item.id.id]
            if isinstance(item, ActionObject):
                self.add_actionobject_read_permissions(
                    context, item, other_node_permissions
                )
            else:
                item = self.transform_item(context, item)  # type: ignore[unreachable]
                res = self.set_object(context, item)

                if res.is_ok():
                    self.add_permissions_for_item(context, item, other_node_permissions)
                else:
                    return SyftError(message=f"Failed to sync {res.err()}")
        return SyftSuccess(message=f"Synced {len(items)} items")

    @service_method(
        path="sync.get_permissions",
        name="get_permissions",
        roles=ADMIN_ROLE_LEVEL,
    )
    def get_permissions(
        self,
        context: AuthedServiceContext,
        items: List[Union[ActionObject, SyftObject]],
    ) -> Dict:
        permissions: Dict = {}

        def get_store(item):  # type: ignore
            if isinstance(item, ActionObject):
                return context.node.get_service("actionservice").store
            elif isinstance(item, Job):
                return context.node.get_service("jobservice").stash.partition
            else:
                return None

        for item in items:
            store = get_store(item)
            if store is not None:
                _id = item.id.id
                permissions[item.id.id] = store.permissions[_id]
        return permissions

    @service_method(
        path="sync.get_state",
        name="get_state",
        roles=ADMIN_ROLE_LEVEL,
    )
    def get_state(
        self, context: AuthedServiceContext, add_to_store: bool = False
    ) -> Union[SyncState, SyftError]:
        new_state = SyncState()

        node = cast(AbstractNode, context.node)

        services_to_sync = [
            "projectservice",
            "requestservice",
            "usercodeservice",
            "jobservice",
            "logservice",
            "outputservice",
            "usercodestatusservice",
        ]

        for service_name in services_to_sync:
            service = node.get_service(service_name)
            items = service.get_all(context)
            new_state.add_objects(items, api=node.root_client.api)  # type: ignore

        # TODO workaround, we only need action objects from outputs for now
        action_object_ids = set()
        for obj in new_state.objects.values():
            if isinstance(obj, ExecutionOutput):
                action_object_ids |= set(obj.output_id_list)
            elif isinstance(obj, Job) and obj.result is not None:
                action_object_ids.add(obj.result.id)

        action_objects = []
        for uid in action_object_ids:
            action_object = node.get_service("actionservice").get(context, uid)  # type: ignore
            if action_object.is_err():
                return SyftError(message=action_object.err())
            action_objects.append(action_object.ok())
        new_state.add_objects(action_objects)

        new_state._build_dependencies(api=node.root_client.api)  # type: ignore

        new_state.permissions = self.get_permissions(context, new_state.objects)

        previous_state = self.stash.get_latest(context=context)
        if previous_state is not None:
            new_state.previous_state_link = LinkedObject.from_obj(
                obj=previous_state,
                service_type=SyncService,
                node_uid=context.node.id,  # type: ignore
            )

        if add_to_store:
            self.stash.set(context.credentials, new_state)

        return new_state
