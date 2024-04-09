# stdlib
from datetime import timedelta
import html
from typing import Any
from typing import Optional

from syft.service.code.user_code import UserCode
from syft.service.job.job_stash import Job
from syft.service.request.request import Request
from syft.util.colors import SURFACE
from syft.util.fonts import ITABLES_CSS, FONT_CSS
from ...util import options
from ...util.notebook_ui.notebook_addons import create_table_template

# relative
from ...serde.serializable import serializable
from ...store.linked_obj import LinkedObject
from ...types.datetime import DateTime
from ...types.syft_object import SYFT_OBJECT_VERSION_1, get_repr_values_table
from ...types.syft_object import SyftObject
from ...types.syncable_object import SyncableSyftObject
from ...types.uid import LineageID
from ...types.uid import UID
from ..context import AuthedServiceContext


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
    previous_object: SyftObject | None = None
    current_state: str
    previous_state: str
    level: int = 0

    __syft_include_id_coll_repr__ = False

    # TODO table formatting
    __repr_attrs__ = [
        "previous_state",
        "current_state",
    ]

    def main_object_description_str(self) -> str:
        if isinstance(self.object, UserCode):
            return self.object.service_func_name
        elif isinstance(self.object, Job):
            return self.object.user_code_name
        elif isinstance(self.object, Request):
            # TODO: handle other requests
            return f"Execute {self.object.code.service_func_name}"
        else:
            return ""

    def type_badge_class(self) -> str:
        if isinstance(self.object, UserCode):
            return "label-light-blue"
        elif isinstance(self.object, Job):
            return "label-light-blue"
        elif isinstance(self.object, Request):
            # TODO: handle other requests
            return "label-light-purple"
        else:
            return ""

    def status_badge(self) -> dict[str, str]:

        status = self.status
        if status == "NEW":
            badge_color = "label-green"
        elif status == "SAME":
            badge_color = "label-gray"
        else:
            badge_color = "label-red"
        return {"value": status.upper(), "type": badge_color}

    def _coll_repr_(self) -> dict[str, Any]:
        # current_state = f"{self.status}\n{self.current_state}"
        # previous_state = f"{self.status}\n{self.previous_state}"
            # "previous_state": html.escape(previous_state),
            # "current_state": html.escape(current_state),

        type_html = f'<div class="label {self.type_badge_class()}">{self.object.__class__.__name__.upper()}</div>'
        
        object_description_str = "   " + self.main_object_description_str()
        description_html = f"<span class='syncstate-description'>{object_description_str}</span>"
        updated_delta_str = "29m ago"
        updated_by = "john@doe.org"
        lower_status_str = "Status: approved • "
        id_div = str(self.id)[:5]
        summary_str = f"""
<div style="display: flex; gap: 8px; justify-content: space-between; width: 100%;">
<div>
{type_html} {description_html}
</div>
<div>
 {id_div}
</div>
</div>
<div style="display: table-row">
<span class='syncstate-col-footer'>{lower_status_str} Updated by {updated_by} {updated_delta_str}</span>
</div>
"""

        # otherwise newlines are replaced with <br>
        summary_str = summary_str.replace("\n", "")
        return {
            "Status": self.status_badge(),
            "Summary": summary_str,
            "Last Sync": "n/a",
        }

    @property
    def object_type(self) -> str:
        prefix = get_hierarchy_level_prefix(self.level)
        return f"{prefix}{type(self.object).__name__}"

    @property
    def status(self) -> str:
        # TODO use Diffs to determine status
        if self.previous_object is None:
            return "NEW"
        elif self.previous_object.syft_eq(ext_obj=self.object):
            return "SAME"
        else:
            return "MODIFIED"

def td_format(td_object):
    seconds = int(td_object.total_seconds())
    periods = [
        ('year',        60*60*24*365),
        ('month',       60*60*24*30),
        ('day',         60*60*24),
        ('hour',        60*60),
        ('minute',      60),
        ('second',      1)
    ]

    strings=[]
    for period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value , seconds = divmod(seconds, period_seconds)
            has_s = 's' if period_value > 1 else ''
            strings.append("%s %s%s" % (period_value, period_name, has_s))

    return ", ".join(strings)


@serializable()
class SyncState(SyftObject):
    __canonical_name__ = "SyncState"
    __version__ = SYFT_OBJECT_VERSION_1

    node_uid: UID
    node_name: str
    objects: dict[UID, SyncableSyftObject] = {}
    dependencies: dict[UID, list[UID]] = {}
    created_at: DateTime = DateTime.now()
    previous_state_link: LinkedObject | None = None
    permissions: dict[UID, set[str]] = {}
    storage_permissions: dict[UID, set[UID]] = {}
    ignored_batches: dict[UID, int] = {}

    # NOTE importing NodeDiff annotation with TYPE_CHECKING does not work here,
    # since typing.get_type_hints does not check for TYPE_CHECKING-imported types
    _previous_state_diff: Any = None

    __attr_searchable__ = ["created_at"]

    def _set_previous_state_diff(self) -> None:
        # relative
        from .diff_state import NodeDiff

        # Re-use NodeDiff to compare to previous state
        # Low = previous state, high = current state
        # NOTE No previous sync state means everything is new
        previous_state = self.previous_state or SyncState(node_uid=self.node_uid, node_name=self.node_name)
        self._previous_state_diff = NodeDiff.from_sync_state(
            previous_state,
            self,
            _include_node_status=False,
        )

    def get_previous_state_diff(self) -> Any:
        if self._previous_state_diff is None:
            self._set_previous_state_diff()

        return self._previous_state_diff

    @property
    def previous_state(self) -> Optional["SyncState"]:
        if self.previous_state_link is not None:
            return self.previous_state_link.resolve
        return None

    @property
    def all_ids(self) -> set[UID]:
        return set(self.objects.keys())

    def get_status(self, uid: UID) -> str | None:
        previous_state_diff = self.get_previous_state_diff()
        if previous_state_diff is None:
            return None
        diff = previous_state_diff.obj_uid_to_diff.get(uid)

        if diff is None:
            return None
        return diff.status

    def add_objects(
        self, objects: list[SyncableSyftObject], context: AuthedServiceContext
    ) -> None:
        for obj in objects:
            if isinstance(obj.id, LineageID):
                self.objects[obj.id.id] = obj
            else:
                self.objects[obj.id] = obj

        # TODO might get slow with large states,
        # need to build dependencies every time to not have UIDs
        # in dependencies that are not in objects
        self._build_dependencies(context=context)

    def _build_dependencies(self, context: AuthedServiceContext) -> None:
        self.dependencies = {}

        all_ids = self.all_ids
        for obj in self.objects.values():
            if hasattr(obj, "get_sync_dependencies"):
                deps = obj.get_sync_dependencies(context=context)
                deps = [d.id for d in deps if d.id in all_ids]  # type: ignore
                # TODO: Why is this en check here? here?
                if len(deps):
                    self.dependencies[obj.id.id] = deps

    @property
    def rows(self) -> list[SyncStateRow]:
        result = []
        ids = set()

        previous_diff = self.get_previous_state_diff()
        if previous_diff is None:
            raise ValueError("No previous state to compare to")
        for batch in previous_diff.batches:
            diff = batch.root_diff
            if diff.object_id in ids:
                continue
            ids.add(diff.object_id)
            row = SyncStateRow(
                object=diff.high_obj,
                previous_object=diff.low_obj,
                current_state=diff.diff_side_str("high"),
                previous_state=diff.diff_side_str("low"),
                level=0,  # TODO add levels to table
            )
            result.append(row)
        return result

    def _repr_html_(self) -> str:
        prop_template = "<p class='paragraph-sm'><strong><span class='pr-8'>{}: </span></strong>{}</p>"
        name_html = prop_template.format('name', self.node_name)
        if self.previous_state_link is not None:
            previous_state = self.previous_state_link.resolve
            delta = timedelta(seconds= self.created_at.utc_timestamp - previous_state.created_at.utc_timestamp)
            val = f"{td_format(delta)} ago"
            date_html = prop_template.format('last sync', val)
        else:
            date_html = prop_template.format('last sync', 'not synced yet')

        repr = f"""
        <style>
            {FONT_CSS}
            .syft-syncstate {{color: {SURFACE[options.color_theme]};}}
            .syft-syncstate h3,
            .syft-syncstate p
              {{font-family: 'Open Sans';}}
              {ITABLES_CSS}
              {{font-family: 'Open Sans';}}
              {ITABLES_CSS}
            </style>
        <div class='syft-syncstate'>
            <h2> SyncState </h2>
            {name_html}
            {date_html}
        </div>
"""

        rews = get_repr_values_table(self.rows, True)
        return repr + create_table_template(rews, "SyncStateRow", grid_template_columns="auto auto 1fr auto", grid_template_cell_columns="unset", table_icon=None)

