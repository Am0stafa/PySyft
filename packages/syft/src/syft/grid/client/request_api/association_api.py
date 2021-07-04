# stdlib
from typing import Any
from typing import Callable
from typing import Dict
from typing import Type

# relative
from ....core.node.common.node import Node
from ....core.node.domain.enums import AssociationRequestResponses
from ....core.node.domain.enums import RequestAPIFields
from ....core.node.domain.enums import ResponseObjectEnum
from ...messages.association_messages import DeleteAssociationRequestMessage
from ...messages.association_messages import GetAssociationRequestMessage
from ...messages.association_messages import GetAssociationRequestsMessage
from ...messages.association_messages import RespondAssociationRequestMessage
from ...messages.association_messages import SendAssociationRequestMessage
from ..exceptions import PyGridClientException
from .request_api import GridRequestAPI


class AssociationRequestAPI(GridRequestAPI):
    def __init__(self, node: Type[Node]):
        super().__init__(
            node=node,
            create_msg=SendAssociationRequestMessage,
            get_msg=GetAssociationRequestMessage,
            get_all_msg=GetAssociationRequestsMessage,
            delete_msg=DeleteAssociationRequestMessage,
            response_key=ResponseObjectEnum.ASSOCIATION_REQUEST,
        )

    def update(self, **kwargs: Any) -> Dict[Any, Any]:  # type: ignore
        raise PyGridClientException(
            "You can not update an association request, try to send another one instead."
        )

    def __getitem__(self, key: int) -> Any:
        return self.get(association_id=key)

    def __delitem__(self, key: int) -> None:
        self.delete(association_id=key)

    def to_obj(self, result: Dict[Any, Any]) -> Any:
        _association_obj = super().to_obj(result)

        _content = {
            RequestAPIFields.TARGET: _association_obj.address,
            RequestAPIFields.NODE_NAME: _association_obj.node,
        }

        def _accept() -> Dict[str, str]:
            _content[RequestAPIFields.RESPONSE] = AssociationRequestResponses.ACCEPT
            return self.perform_api_request(
                syft_msg=RespondAssociationRequestMessage, content=_content
            )

        def _deny() -> Dict[str, str]:
            _content[RequestAPIFields.RESPONSE] = AssociationRequestResponses.DENY
            return self.perform_api_request(
                syft_msg=RespondAssociationRequestMessage, content=_content
            )

        _association_obj.accept = _accept
        _association_obj.deny = _deny

        return _association_obj
