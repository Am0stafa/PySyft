"""
How to check differences between two objects:
    * by default merge every attr
    * check if there is a custom implementation of the check function
    * check if there are exceptions we do not want to merge
    * check if there are some restrictions on the attr set
"""

# stdlib
import html
import textwrap
from typing import Any
from typing import ClassVar
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Type
from typing import Union

# third party
from pydantic import model_validator
from rich import box
from rich.console import Console
from rich.console import Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from typing_extensions import Self

# relative
from ...types.syft_object import SYFT_OBJECT_VERSION_1
from ...types.syft_object import SyftObject
from ...types.uid import LineageID
from ...types.uid import UID
from ...util import options
from ...util.colors import SURFACE
from ...util.fonts import ITABLES_CSS
from ...util.fonts import fonts_css
from ..action.action_object import ActionObject
from ..action.action_permissions import ActionObjectPermission
from ..code.user_code import UserCode
from ..code.user_code import UserCodeStatusCollection
from ..job.job_stash import Job
from ..log.log import SyftLog
from ..output.output_service import ExecutionOutput
from ..request.request import Request
from ..response import SyftError
from .sync_state import SyncState

sketchy_tab = "‎ " * 4


class AttrDiff(SyftObject):
    # version
    __canonical_name__ = "AttrDiff"
    __version__ = SYFT_OBJECT_VERSION_1
    attr_name: str
    low_attr: Any = None
    high_attr: Any = None

    def _repr_html_(self) -> str:
        return f"""{self.attr_name}:
    Low Side value: {self.low_attr}
    High Side value: {self.high_attr}
    """

    def __repr_side__(self, side: str) -> str:
        if side == "low":
            return recursive_attr_repr(self.low_attr)
        else:
            return recursive_attr_repr(self.high_attr)

    def _coll_repr_(self) -> Dict[str, Any]:
        return {
            "attr name": self.attr_name,
            "low attr": html.escape(f"{self.low_attr}"),
            "high attr": html.escape(str(self.high_attr)),
        }


class ListDiff(AttrDiff):
    # version
    __canonical_name__ = "ListDiff"
    __version__ = SYFT_OBJECT_VERSION_1
    diff_ids: List[int] = []
    new_low_ids: List[int] = []
    new_high_ids: List[int] = []

    @property
    def is_empty(self) -> bool:
        return (
            len(self.diff_ids) == 0
            and len(self.new_low_ids) == 0
            and len(self.new_high_ids) == 0
        )

    @classmethod
    def from_lists(cls, attr_name: str, low_list: List, high_list: List) -> "ListDiff":
        diff_ids = []
        new_low_ids = []
        new_high_ids = []
        if len(low_list) != len(high_list):
            if len(low_list) > len(high_list):
                common_length = len(high_list)
                new_low_ids = list(range(common_length, len(low_list)))
            else:
                common_length = len(low_list)
                new_high_ids = list(range(common_length, len(high_list)))
        else:
            common_length = len(low_list)

        for i in range(common_length):
            # if hasattr(low_list[i], 'syft_eq'):
            #     if not low_list[i].syft_eq(high_list[i]):
            #         diff_ids.append(i)
            if low_list[i] != high_list[i]:
                diff_ids.append(i)

        change_diff = ListDiff(
            attr_name=attr_name,
            low_attr=low_list,
            high_attr=high_list,
            diff_ids=diff_ids,
            new_low_ids=new_low_ids,
            new_high_ids=new_high_ids,
        )
        return change_diff


def recursive_attr_repr(value_attr: Union[List, Dict, bytes], num_tabs: int = 0) -> str:
    new_num_tabs = num_tabs + 1

    if isinstance(value_attr, list):
        list_repr = "[\n"
        for elem in value_attr:
            list_repr += recursive_attr_repr(elem, num_tabs=num_tabs + 1) + "\n"
        list_repr += "]"
        return list_repr

    elif isinstance(value_attr, dict):
        dict_repr = "{\n"
        for key, elem in value_attr.items():
            dict_repr += f"{sketchy_tab * new_num_tabs}{key}: {str(elem)}\n"
        dict_repr += "}"
        return dict_repr

    elif isinstance(value_attr, bytes):
        value_attr = repr(value_attr)  # type: ignore
        if len(value_attr) > 50:
            value_attr = value_attr[:50] + "..."  # type: ignore
    return f"{sketchy_tab*num_tabs}{str(value_attr)}"


class ObjectDiff(SyftObject):  # StateTuple (compare 2 objects)
    # version
    __canonical_name__ = "ObjectDiff"
    __version__ = SYFT_OBJECT_VERSION_1
    low_obj: Optional[SyftObject] = None
    high_obj: Optional[SyftObject] = None
    low_permissions: List[ActionObjectPermission] = []
    high_permissions: List[ActionObjectPermission] = []

    new_low_permissions: List[ActionObjectPermission] = []
    new_high_permissions: List[ActionObjectPermission] = []
    obj_type: Type
    diff_list: List[AttrDiff] = []

    __repr_attrs__ = [
        "low_state",
        "high_state",
    ]

    @classmethod
    def from_objects(
        cls,
        low_obj: Optional[SyftObject],
        high_obj: Optional[SyftObject],
        low_permissions: List[ActionObjectPermission],
        high_permissions: List[ActionObjectPermission],
    ) -> "ObjectDiff":
        if low_obj is None and high_obj is None:
            raise ValueError("Both low and high objects are None")
        obj_type = type(low_obj if low_obj is not None else high_obj)

        if low_obj is None or high_obj is None:
            diff_list = []
        else:
            diff_list = low_obj.get_diffs(high_obj)

        return cls(
            low_obj=low_obj,
            high_obj=high_obj,
            obj_type=obj_type,
            low_permissions=low_permissions,
            high_permissions=high_permissions,
            diff_list=diff_list,
        )

    def __hash__(self) -> int:
        return hash(self.id) + hash(self.low_obj) + hash(self.high_obj)

    @property
    def status(self) -> str:
        if self.low_obj is None or self.high_obj is None:
            return "NEW"
        if len(self.diff_list) == 0:
            return "SAME"
        return "DIFF"

    @property
    def object_id(self) -> UID:
        uid: Union[UID, LineageID] = (
            self.low_obj.id if self.low_obj is not None else self.high_obj.id  # type: ignore
        )
        if isinstance(uid, LineageID):
            return uid.id
        return uid

    @property
    def non_empty_object(self) -> Optional[SyftObject]:
        return self.low_obj or self.high_obj

    @property
    def object_type(self) -> str:
        return self.obj_type.__name__

    @property
    def high_state(self) -> str:
        return self.state_str("high")

    @property
    def low_state(self) -> str:
        return self.state_str("low")

    @property
    def object_uid(self) -> UID:
        return self.low_obj.id if self.low_obj is not None else self.high_obj.id  # type: ignore

    def diff_attributes_str(self, side: str) -> str:
        obj = self.low_obj if side == "low" else self.high_obj

        if obj is None:
            return ""

        repr_attrs = getattr(obj, "__repr_attrs__", [])
        if self.status == "SAME":
            repr_attrs = repr_attrs[:3]

        if self.status in {"SAME", "NEW"}:
            attrs_str = ""
            for attr in repr_attrs:
                value = getattr(obj, attr)
                attrs_str += f"{attr}: {recursive_attr_repr(value)}\n"
            return attrs_str

        elif self.status == "DIFF":
            attrs_str = ""
            for diff in self.diff_list:
                attrs_str += f"{diff.attr_name}: {diff.__repr_side__(side)}\n"
            return attrs_str
        else:
            raise ValueError("")

    def diff_side_str(self, side: str) -> str:
        obj = self.low_obj if side == "low" else self.high_obj
        if obj is None:
            return ""
        res = f"{self.obj_type.__name__.upper()} #{obj.id}:\n"
        res += self.diff_attributes_str(side)
        return res

    def state_str(self, side: str) -> str:
        other_obj: Optional[SyftObject] = None
        if side == "high":
            obj = self.high_obj
            other_obj = self.low_obj
        else:
            obj = self.low_obj
            other_obj = self.high_obj

        if obj is None:
            return "-"
        if self.status == "SAME":
            return f"SAME\n{self.obj_type.__name__}"

        if isinstance(obj, ActionObject):
            return obj.__repr__()

        if other_obj is None:  # type: ignore[unreachable]
            attrs_str = ""
            attrs = getattr(obj, "__repr_attrs__", [])
            for attr in attrs:
                value = getattr(obj, attr)
                attrs_str += f"{sketchy_tab}{attr} = {recursive_attr_repr(value)}\n"
            attrs_str = attrs_str[:-1]
            return f"NEW\n\nclass {self.object_type}:\n{attrs_str}"

        attr_text = f"DIFF\nclass {self.object_type}:\n"
        for diff in self.diff_list:
            # TODO
            attr_text += (
                f"{sketchy_tab}{diff.attr_name}={diff.__repr_side__(side)}," + "\n"
            )
        if len(self.diff_list) > 0:
            attr_text = attr_text[:-2]

        return attr_text

    def get_obj(self) -> Optional[SyftObject]:
        if self.status == "NEW":
            return self.low_obj if self.low_obj is not None else self.high_obj
        else:
            raise ValueError("ERROR")

    def _coll_repr_(self) -> Dict[str, Any]:
        low_state = f"{self.status}\n{self.diff_side_str('low')}"
        high_state = f"{self.status}\n{self.diff_side_str('high')}"
        return {
            "low_state": html.escape(low_state),
            "high_state": html.escape(high_state),
        }

    def _repr_html_(self) -> str:
        if self.low_obj is None and self.high_obj is None:
            return SyftError(message="Something broke")

        base_str = f"""
        <style>
        {fonts_css}
        .syft-dataset {{color: {SURFACE[options.color_theme]};}}
        .syft-dataset h3,
        .syft-dataset p
            {{font-family: 'Open Sans';}}
            {ITABLES_CSS}
        </style>
        <div class='syft-diff'>
        """

        obj_repr: str
        attr_text: str
        if self.low_obj is None:
            if hasattr(self.high_obj, "_repr_html_"):
                obj_repr = self.high_obj._repr_html_()  # type: ignore
            elif hasattr(self.high_obj, "_inner_repr"):
                obj_repr = self.high_obj._inner_repr()  # type: ignore
            else:
                obj_repr = self.__repr__()
            attr_text = (
                f"""
    <h3>{self.object_type} ObjectDiff (New {self.object_type}  on the High Side):</h3>
    """
                + obj_repr
            )

        elif self.high_obj is None:
            if hasattr(self.low_obj, "_repr_html_"):
                obj_repr = self.low_obj._repr_html_()  # type: ignore
            elif hasattr(self.low_obj, "_inner_repr"):
                obj_repr = self.low_obj._inner_repr()  # type: ignore
            else:
                obj_repr = self.__repr__()
            attr_text = (
                f"""
    <h3>{self.object_type} ObjectDiff (New {self.object_type}  on the High Side):</h3>
    """
                + obj_repr
            )

        elif self.status == "SAME":
            obj_repr = "No changes between low side and high side"
        else:
            obj_repr = ""
            for diff in self.diff_list:
                obj_repr += diff.__repr__() + "<br>"

            obj_repr = obj_repr.replace("\n", "<br>")
            # print("New lines", res)

        attr_text = f"<h3>{self.object_type} ObjectDiff:</h3>\n{obj_repr}"
        return base_str + attr_text


def _wrap_text(text: str, width: int, indent: int = 4) -> str:
    """Wrap text, preserving existing line breaks"""
    return "\n".join(
        [
            "\n".join(
                textwrap.wrap(
                    line,
                    width,
                    break_long_words=False,
                    replace_whitespace=False,
                    subsequent_indent=" " * indent,
                )
            )
            for line in text.splitlines()
            if line.strip() != ""
        ]
    )


class ObjectDiffBatch(SyftObject):
    __canonical_name__ = "DiffHierarchy"
    __version__ = SYFT_OBJECT_VERSION_1
    LINE_LENGTH: ClassVar[int] = 100
    INDENT: ClassVar[int] = 4
    ORDER: ClassVar[Dict] = {"low": 0, "high": 1}

    # Diffs are ordered in depth-first order,
    # so the first diff is the root of the hierarchy
    diffs: List[ObjectDiff]
    hierarchy_levels: List[int]
    dependencies: Dict[UID, List[UID]] = {}
    dependents: Dict[UID, List[UID]] = {}

    @property
    def visual_hierarchy(self) -> Tuple[Type, dict]:
        # Returns
        root_obj: Union[Request, UserCodeStatusCollection, ExecutionOutput, Any] = (
            self.root.low_obj if self.root.low_obj is not None else self.root.high_obj
        )
        if isinstance(root_obj, Request):
            return Request, {
                Request: [UserCode],
                UserCode: [UserCode],
            }
        if isinstance(root_obj, UserCodeStatusCollection):
            return UserCode, {
                UserCode: [UserCodeStatusCollection],
            }
        if isinstance(root_obj, ExecutionOutput):
            return UserCode, {
                UserCode: [Job],
                Job: [ExecutionOutput, SyftLog, Job],
                ExecutionOutput: [ActionObject],
            }
        raise ValueError(f"Unknown root type: {self.root.obj_type}")

    @model_validator(mode="after")
    def make_dependents(self) -> Self:
        dependents: Dict = {}
        for parent, children in self.dependencies.items():
            for child in children:
                dependents[child] = dependents.get(child, []) + [parent]
        self.dependents = dependents
        return self

    @property
    def root(self) -> ObjectDiff:
        return self.diffs[0]

    def __len__(self) -> int:
        return len(self.diffs)

    def __repr__(self) -> str:
        return f"""{self.hierarchy_str('low')}

{self.hierarchy_str('high')}
"""

    def _repr_markdown_(self, wrap_as_python: bool = True, indent: int = 0) -> str:
        return ""  # Turns off the _repr_markdown_ of SyftObject

    def _get_visual_hierarchy(self, node: ObjectDiff) -> dict[ObjectDiff, dict]:
        _, child_types_map = self.visual_hierarchy
        child_types = child_types_map.get(node.obj_type, [])
        dep_ids = self.dependencies.get(node.object_id, []) + self.dependents.get(
            node.object_id, []
        )

        result = {}
        for child_type in child_types:
            children = [
                n
                for n in self.diffs
                if n.object_id in dep_ids
                and isinstance(n.low_obj or n.high_obj, child_type)
            ]
            for child in children:
                result[child] = self._get_visual_hierarchy(child)

        return result

    def get_visual_hierarchy(self) -> "ObjectDiffBatch":
        visual_root_type = self.visual_hierarchy[0]
        # First diff with a visual root type is the visual root
        # because diffs are in depth-first order
        visual_root = [
            diff
            for diff in self.diffs
            if isinstance(diff.low_obj or diff.high_obj, visual_root_type)
        ][0]
        return {visual_root: self._get_visual_hierarchy(visual_root)}  # type: ignore

    def _get_obj_str(self, diff_obj: ObjectDiff, level: int, side: str) -> str:
        obj = diff_obj.low_obj if side == "low" else diff_obj.high_obj
        if obj is None:
            return ""
        indent = " " * level * self.INDENT
        obj_str = diff_obj.diff_side_str(side)
        obj_str = _wrap_text(obj_str, width=self.LINE_LENGTH - len(indent))

        line_prefix = indent + f"―――― {diff_obj.status} "
        line = "―" * (self.LINE_LENGTH - len(line_prefix))
        return f"""{line_prefix}{line}

{textwrap.indent(obj_str, indent)}

"""

    def hierarchy_str(self, side: str) -> str:
        def _hierarchy_str_recursive(tree: Dict, level: int) -> str:
            result = ""
            for node, children in tree.items():
                result += self._get_obj_str(node, level, side)
                result += _hierarchy_str_recursive(children, level + 1)
            return result

        visual_hierarchy = self.get_visual_hierarchy()
        res = _hierarchy_str_recursive(visual_hierarchy, 0)
        if res == "":
            res = f"No {side} side changes."
        return f"""{side.upper()} SIDE STATE:

{res}"""


class NodeDiff(SyftObject):
    __canonical_name__ = "NodeDiff"
    __version__ = SYFT_OBJECT_VERSION_1

    obj_uid_to_diff: Dict[UID, ObjectDiff] = {}
    dependencies: Dict[UID, List[UID]] = {}

    @classmethod
    def from_sync_state(
        cls: Type["NodeDiff"], low_state: SyncState, high_state: SyncState
    ) -> "NodeDiff":
        obj_uid_to_diff = {}
        for obj_id in set(low_state.objects.keys()) | set(high_state.objects.keys()):
            low_obj = low_state.objects.get(obj_id, None)
            low_permissions: List = low_state.permissions.get(obj_id, [])
            high_obj = high_state.objects.get(obj_id, None)
            high_permissions: List = high_state.permissions.get(obj_id, [])
            diff = ObjectDiff.from_objects(
                low_obj, high_obj, low_permissions, high_permissions
            )
            obj_uid_to_diff[diff.object_id] = diff

        node_diff = cls(obj_uid_to_diff=obj_uid_to_diff)

        node_diff._init_dependencies(low_state, high_state)
        return node_diff

    def _init_dependencies(self, low_state: SyncState, high_state: SyncState) -> None:
        all_parents = set(low_state.dependencies.keys()) | set(
            high_state.dependencies.keys()
        )
        for parent in all_parents:
            low_deps = low_state.dependencies.get(parent, [])
            high_deps = high_state.dependencies.get(parent, [])
            self.dependencies[parent] = list(set(low_deps) | set(high_deps))

    @property
    def diffs(self) -> List[ObjectDiff]:
        diffs_depthfirst = [
            diff for hierarchy in self.hierarchies for diff in hierarchy.diffs
        ]
        # deduplicate
        diffs = []
        ids = set()
        for diff in diffs_depthfirst:
            if diff.object_id not in ids:
                diffs.append(diff)
                ids.add(diff.object_id)
        return diffs

    def _repr_html_(self) -> Any:
        return self.diffs._repr_html_()

    def _sort_hierarchies(
        self, hierarchies: List[ObjectDiffBatch]
    ) -> List[ObjectDiffBatch]:
        without_usercode = []
        grouped_by_usercode: Dict[UID, List[ObjectDiffBatch]] = {}
        for hierarchy in hierarchies:
            has_usercode = False
            for diff in hierarchy.diffs:
                obj = diff.low_obj if diff.low_obj is not None else diff.high_obj
                if isinstance(obj, UserCode):
                    grouped_by_usercode[obj.id] = hierarchy
                    has_usercode = True
                    break
            if not has_usercode:
                without_usercode.append(hierarchy)

        # Order of hierarchies, by root object type
        hierarchy_order = [UserCodeStatusCollection, Request, ExecutionOutput]
        # Sort group by hierarchy_order, then by root object id
        for hierarchy_group in grouped_by_usercode.values():
            hierarchy_group.sort(
                key=lambda x: (
                    hierarchy_order.index(x.root.obj_type),
                    x.root.object_id,
                )
            )

        # sorted = sorted groups + without_usercode
        sorted_hierarchies = []
        for grp in grouped_by_usercode.values():
            sorted_hierarchies.extend(grp)
        sorted_hierarchies.extend(without_usercode)
        return sorted_hierarchies

    @property
    def hierarchies(self) -> List[ObjectDiffBatch]:
        # Returns a list of hierarchies, where each hierarchy is a list of tuples (ObjectDiff, level),
        # in depth-first order.

        # Each hierarchy only contains one root, at the first position
        # Example: [(Diff1, 0), (Diff2, 1), (Diff3, 2), (Diff4, 1)]
        # Diff1
        # -- Diff2
        # ---- Diff3
        # -- Diff4

        def _build_hierarchy_helper(
            uid: UID, level: int = 0, visited: Optional[Set] = None
        ) -> List:
            visited = visited if visited is not None else set()

            if uid in visited:
                return []

            result = [(uid, level)]
            visited.add(uid)
            if uid in self.dependencies:
                deps = self.dependencies[uid]
                for dep_uid in self.dependencies[uid]:
                    if dep_uid not in visited:
                        # NOTE we pass visited + deps to recursive calls, to have
                        # all objects at the highest level in the hierarchy
                        # Example:
                        # ExecutionOutput
                        # -- Job
                        # ---- Result
                        # -- Result
                        # We want to omit Job.Result, because it's already in ExecutionOutput.Result
                        result.extend(
                            _build_hierarchy_helper(
                                uid=dep_uid,
                                level=level + 1,
                                visited=visited | set(deps) - {dep_uid},
                            )
                        )
            return result

        hierarchies = []
        all_ids = set(self.obj_uid_to_diff.keys())
        child_ids = {child for deps in self.dependencies.values() for child in deps}
        # Root ids are object ids with no parents
        root_ids = list(all_ids - child_ids)

        for root_uid in root_ids:
            uid_hierarchy = _build_hierarchy_helper(root_uid)
            diffs = [self.obj_uid_to_diff[uid] for uid, _ in uid_hierarchy]
            levels = [level for _, level in uid_hierarchy]

            batch_uids = {uid for uid, _ in uid_hierarchy}
            dependencies = {
                uid: [d for d in self.dependencies.get(uid, []) if d in batch_uids]
                for uid in batch_uids
            }

            batch = ObjectDiffBatch(
                diffs=diffs, hierarchy_levels=levels, dependencies=dependencies
            )
            hierarchies.append(batch)

        return hierarchies

    def objs_to_sync(self) -> List[SyftObject]:
        objs: list[SyftObject] = []
        for diff in self.diffs:
            if diff.status == "NEW":
                objs.append(diff.get_obj())
        return objs


class ResolvedSyncState(SyftObject):
    __canonical_name__ = "SyncUpdate"
    __version__ = SYFT_OBJECT_VERSION_1

    create_objs: List[SyftObject] = []
    update_objs: List[SyftObject] = []
    delete_objs: List[SyftObject] = []
    new_permissions: List[ActionObjectPermission] = []
    alias: str

    def add_cruds_from_diff(self, diff: ObjectDiff, decision: str) -> None:
        if diff.status == "SAME":
            return

        my_obj = diff.low_obj if self.alias == "low" else diff.high_obj
        other_obj = diff.low_obj if self.alias == "high" else diff.high_obj

        if decision != self.alias:  # chose for the other
            if diff.status == "DIFF":
                if other_obj not in self.update_objs:
                    self.update_objs.append(other_obj)
            elif diff.status == "NEW":
                if my_obj is None:
                    if other_obj not in self.create_objs:
                        self.create_objs.append(other_obj)
                elif other_obj is None:
                    if my_obj not in self.delete_objs:
                        self.delete_objs.append(my_obj)

    def __repr__(self) -> str:
        return (
            f"ResolvedSyncState(\n"
            f"  create_objs={self.create_objs},\n"
            f"  update_objs={self.update_objs},\n"
            f"  delete_objs={self.delete_objs}\n"
            f"  new_permissions={self.new_permissions}\n"
            f")"
        )


def display_diff_object(obj_state: Optional[str]) -> Panel:
    if obj_state is None:
        return Panel(Markdown("None"), box=box.ROUNDED, expand=False)
    return Panel(
        Markdown(f"```python\n{obj_state}\n```", code_theme="default"),
        box=box.ROUNDED,
        expand=False,
    )


def display_diff_hierarchy(diff_hierarchy: List[Tuple[ObjectDiff, int]]) -> None:
    console = Console()

    for diff, level in diff_hierarchy:
        title = f"{diff.obj_type.__name__}({diff.object_id}) - State: {diff.status}"

        low_side_panel = display_diff_object(diff.low_state if diff.low_obj else None)
        low_side_panel.title = "Low side"
        low_side_panel.title_align = "left"
        high_side_panel = display_diff_object(
            diff.high_state if diff.high_obj is not None else None
        )
        high_side_panel.title = "High side"
        high_side_panel.title_align = "left"

        grouped_panels = Group(low_side_panel, high_side_panel)

        diff_panel = Panel(
            grouped_panels,
            title=title,
            title_align="left",
            box=box.ROUNDED,
            expand=False,
            padding=(1, 2),
        )

        if level > 0:
            diff_panel = Padding(diff_panel, (0, 0, 0, 5 * level))

        console.print(diff_panel)
