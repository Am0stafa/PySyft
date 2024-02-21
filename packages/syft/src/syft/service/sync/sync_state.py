from syft.store.linked_obj import LinkedObject
from ...types.syft_object import SYFT_OBJECT_VERSION_1
from ...types.uid import UID, LineageID
from ...types.syft_object import SyftObject
from typing import Dict, List, Optional, Set, Tuple
from ...types.datetime import DateTime
from ...serde.serializable import serializable


def get_hierarchy_level_prefix(level: int) -> str:
    if level == 0:
        return ""
    else:
        return "--" * level + " "


@serializable()
class SyncStateRow(SyftObject):
    """A row in the SyncState table"""

    __canonical_name__ = "SyncStateItem"
    __version__ = SYFT_OBJECT_VERSION_1

    object: SyftObject
    previous_object: Optional[SyftObject]
    level: int = 0

    # TODO table formatting
    __repr_attrs__ = [
        "object_type_with_level",
        "status",
        "previous_state",
        "current_state",
    ]

    @property
    def object_type(self) -> str:
        return self.object.__canonical_name__

    @property
    def object_type_with_level(self) -> str:
        prefix = get_hierarchy_level_prefix(self.level)
        return f"{prefix}{self.object_type}"

    @property
    def status(self) -> str:
        # TODO use Diffs to determine status
        if self.previous_object is None:
            return "NEW"
        else:
            return "CHANGED"

    @property
    def previous_state(self):
        # TODO display state in table
        if self.previous_object is None:
            return ""
        else:
            return f"{self.object_type}()"

    @property
    def current_state(self):
        # TODO display state in table
        return f"{self.object_type}()"


@serializable()
class SyncState(SyftObject):
    __canonical_name__ = "SyncState"
    __version__ = SYFT_OBJECT_VERSION_1

    objects: Dict[UID, SyftObject] = {}
    dependencies: Dict[UID, List[UID]] = {}
    created_at: DateTime = DateTime.now()
    previous_state_link: Optional[LinkedObject] = None

    __attr_searchable__ = ["created_at"]

    @property
    def previous_state(self) -> Optional["SyncState"]:
        if self.previous_state_link is not None:
            return self.previous_state_link.resolve

    @property
    def all_ids(self) -> Set[UID]:
        return set(self.objects.keys())

    def add_objects(self, objects: List[SyftObject]) -> None:
        for obj in objects:
            if isinstance(obj.id, LineageID):
                uid = obj.id.id
            else:
                uid = obj.id
            self.objects[uid] = obj

        # TODO might get slow with large states,
        # need to build dependencies every time to not have UIDs
        # in dependencies that are not in objects
        self._build_dependencies()

    def _build_dependencies(self) -> None:
        self.dependencies = {}

        all_ids = self.all_ids
        for obj in self.objects.values():
            if hasattr(obj, "get_sync_dependencies"):
                deps = obj.get_sync_dependencies()
                deps = [d.id for d in deps if d.id in all_ids]
                if len(deps):
                    self.dependencies[obj.id] = deps

    @property
    def hierarchies(self) -> List[List[Tuple[SyftObject, int]]]:
        def _build_hierarchy_helper(uid: UID, level: int = 0) -> List[Tuple[UID, int]]:
            result = [(uid, level)]
            if uid in self.dependencies:
                for child_uid in self.dependencies[uid]:
                    result.extend(_build_hierarchy_helper(child_uid, level + 1))
            return result

        result = []
        all_ids = self.all_ids
        child_ids = {child for deps in self.dependencies.values() for child in deps}
        root_ids = list(all_ids - child_ids)

        for root_uid in root_ids:
            uid_hierarchy = _build_hierarchy_helper(root_uid)
            object_hierarchy = [
                (self.objects[uid], level) for uid, level in uid_hierarchy
            ]
            result.append(object_hierarchy)

        return result

    @property
    def rows(self) -> List[SyncStateRow]:
        # Display syncstate as table in hierarchical order
        result = []
        for hierarchy in self.hierarchies:
            for obj, level in hierarchy:
                item = SyncStateRow(
                    object=obj,
                    previous_object=None,  # TODO
                    level=level,
                )
                result.append(item)
        return result
