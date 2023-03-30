# future
from __future__ import annotations

# stdlib
import ast
from enum import Enum
import hashlib
import inspect
from io import StringIO
import sys
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Type
from typing import Union

# third party
from result import Err
from result import Ok
from result import Result

# relative
from .api import NodeView
from .code_parse import GlobalsVisitor
from .context import AuthedServiceContext
from .credentials import SyftVerifyKey
from .dataset import Asset
from .document_store import PartitionKey
from .node import NodeType
from .node_metadata import EnclaveMetadata
from .policy import CustomInputPolicy
from .policy import CustomOutputPolicy
from .policy import InputPolicy
from .policy import OutputPolicy
from .policy import Policy
from .policy import SubmitUserPolicy
from .policy import UserPolicy
from .policy import init_policy
from .policy_service import PolicyService
from .serializable import serializable
from .syft_object import SYFT_OBJECT_VERSION_1
from .syft_object import SyftObject
from .transforms import TransformContext
from .transforms import generate_id
from .transforms import transform
from .uid import UID
from .unparse import unparse

UserVerifyKeyPartitionKey = PartitionKey(key="user_verify_key", type_=SyftVerifyKey)
CodeHashPartitionKey = PartitionKey(key="code_hash", type_=int)

PyCodeObject = Any


def extract_uids(kwargs: Dict[str, Any]) -> Dict[str, UID]:
    # relative
    from .action_object import ActionObject
    from .twin_object import TwinObject

    uid_kwargs = {}
    for k, v in kwargs.items():
        uid = v
        if isinstance(v, ActionObject):
            uid = v.id
        if isinstance(v, TwinObject):
            uid = v.id
        if isinstance(v, Asset):
            uid = v.action_id

        if not isinstance(uid, UID):
            raise Exception(f"Input {k} must have a UID not {type(v)}")

        uid_kwargs[k] = uid
    return uid_kwargs


@serializable()
class UserCodeStatus(Enum):
    SUBMITTED = "submitted"
    DENIED = "denied"
    EXECUTE = "execute"

    def __hash__(self) -> int:
        return hash(self.value)


# User Code status context for multiple approvals
# To make nested dicts hashable for mongodb
# as status is in attr_searchable
@serializable(attrs=["base_dict"])
class UserCodeStatusContext:
    base_dict: Dict = {}

    def __init__(self, base_dict: Dict):
        self.base_dict = base_dict

    def __repr__(self):
        return str(self.base_dict)

    def __hash__(self) -> int:
        hash_sum = 0
        for k, v in self.base_dict.items():
            hash_sum = hash(k) + hash(v)
        return hash_sum

    @property
    def approved(self) -> bool:
        # approved for this node only
        statuses = set(self.base_dict.values())
        return len(statuses) == 1 and UserCodeStatus.EXECUTE in statuses

    def for_context(self, context: AuthedServiceContext) -> UserCodeStatus:
        if context.node.node_type == NodeType.ENCLAVE:
            keys = set(self.base_dict.values())
            if len(keys) == 1 and UserCodeStatus.EXECUTE in keys:
                return UserCodeStatus.EXECUTE
            elif UserCodeStatus.SUBMITTED in keys and UserCodeStatus.DENIED not in keys:
                return UserCodeStatus.SUBMITTED
            elif UserCodeStatus.DENIED in keys:
                return UserCodeStatus.DENIED
            else:
                return Exception(f"Invalid types in {keys} for Code Submission")

        elif context.node.node_type == NodeType.DOMAIN:
            node_view = NodeView(
                node_name=context.node.name,
                verify_key=context.node.signing_key.verify_key,
            )
            if node_view in self.base_dict:
                return self.base_dict[node_view]
            else:
                raise Exception(
                    f"Code Object does not contain {context.node.name} Domain's data"
                )
        else:
            raise Exception(
                f"Invalid Node Type for Code Submission:{context.node.node_type}"
            )

    def mutate(
        self, value: UserCodeStatus, node_name: str, verify_key: SyftVerifyKey
    ) -> Result[Ok, Err]:
        node_view = NodeView(node_name=node_name, verify_key=verify_key)
        base_dict = self.base_dict
        if node_view in base_dict:
            base_dict[node_view] = value
            setattr(self, "base_dict", base_dict)
            return Ok(self)
        else:
            return Err(
                "Cannot Modify Status as the Domain's data is not included in the request"
            )


@serializable()
class UserCode(SyftObject):
    # version
    __canonical_name__ = "UserCode"
    __version__ = SYFT_OBJECT_VERSION_1

    id: UID
    user_verify_key: SyftVerifyKey
    raw_code: str
    input_policy_type: Union[Type[InputPolicy], UserPolicy]
    input_policy_init_kwargs: Optional[Dict[Any, Any]] = None
    input_policy_state: Optional[InputPolicy]
    output_policy_type: Union[Type[OutputPolicy], UserPolicy]
    output_policy_init_kwargs: Optional[Dict[Any, Any]] = None
    output_policy_state: Optional[OutputPolicy]
    parsed_code: str
    service_func_name: str
    unique_func_name: str
    user_unique_func_name: str
    code_hash: str
    signature: inspect.Signature
    status: UserCodeStatusContext
    input_kwargs: List[str]
    enclave_metadata: Optional[EnclaveMetadata] = None

    __attr_searchable__ = ["user_verify_key", "status", "service_func_name"]
    __attr_unique__ = ["code_hash", "user_unique_func_name"]
    __attr_repr_cols__ = ["status", "service_func_name"]

    @property
    def input_policy(self) -> Optional[InputPolicy]:
        if not self.status.approved:
            return None

        if self.input_policy_state is None:
            if isinstance(self.input_policy_type, type) and issubclass(
                self.input_policy_type, InputPolicy
            ):
                self.input_policy_state = self.input_policy_type(
                    **self.input_policy_init_kwargs
                )
            elif isinstance(self.input_policy_type, UserPolicy):
                self.input_policy_state = init_policy(
                    self.input_policy_type, self.input_policy_init_kwargs
                )
            else:
                raise Exception(f"Invalid output_policy_type: {self.input_policy_type}")
        return self.input_policy_state

    @property
    def output_policy(self) -> Optional[OutputPolicy]:
        if not self.status.approved:
            return None
        print("self.output_policy_type", self.output_policy_type)
        print("self.output_policy_type", type(self.output_policy_type))
        print("self.output_policy_type", type(self.output_policy_type) == type)
        if isinstance(self.output_policy_type, type):
            print("self.output_policy_type mro", self.output_policy_type.mro())
        if self.output_policy_state is None:
            if isinstance(self.output_policy_type, type) and issubclass(
                self.output_policy_type, OutputPolicy
            ):
                self.output_policy_state = self.output_policy_type(
                    **self.output_policy_init_kwargs
                )
            elif isinstance(self.output_policy_type, UserPolicy):
                self.output_policy_state = init_policy(
                    self.output_policy_type, self.output_policy_init_kwargs
                )
            else:
                raise Exception(
                    f"Invalid output_policy_type: {self.output_policy_type}"
                )
        return self.output_policy_state

    @property
    def byte_code(self) -> Optional[PyCodeObject]:
        return compile_byte_code(self.parsed_code)

    @property
    def unsafe_function(self) -> Optional[Callable]:
        print("WARNING: This code was submitted by a User and could be UNSAFE.")

        # 🟡 TODO: re-use the same infrastructure as the execute_byte_code function
        def wrapper(*args: Any, **kwargs: Any) -> Callable:
            # remove the decorator
            inner_function = ast.parse(self.raw_code).body[0]
            inner_function.decorator_list = []
            # compile the function
            raw_byte_code = compile_byte_code(unparse(inner_function))
            # load it
            exec(raw_byte_code)  # nosec
            # execute it
            evil_string = f"{self.service_func_name}(*args, **kwargs)"
            result = eval(evil_string, None, locals())  # nosec
            # return the results
            return result

        return wrapper

    @property
    def code(self) -> str:
        return self.raw_code


@serializable(without=["local_function"])
class SubmitUserCode(SyftObject):
    # version
    __canonical_name__ = "SubmitUserCode"
    __version__ = SYFT_OBJECT_VERSION_1

    id: Optional[UID]
    code: str
    func_name: str
    signature: inspect.Signature
    input_policy_type: Union[SubmitUserPolicy, UID, Type[InputPolicy]]
    input_policy_init_kwargs: Optional[Dict[Any, Any]] = {}
    output_policy_type: Union[SubmitUserPolicy, UID, Type[OutputPolicy]]
    output_policy_init_kwargs: Optional[Dict[Any, Any]] = {}
    local_function: Optional[Callable]
    input_kwargs: List[str]
    enclave_metadata: Optional[EnclaveMetadata] = None

    @property
    def kwargs(self) -> List[str]:
        return self.input_policy.inputs

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # only run this on the client side
        if self.local_function:
            # filtered_args = []
            filtered_kwargs = {}
            # for arg in args:
            #     filtered_args.append(debox_asset(arg))
            for k, v in kwargs.items():
                filtered_kwargs[k] = debox_asset(v)

            return self.local_function(**filtered_kwargs)
        else:
            raise NotImplementedError


def debox_asset(arg: Any) -> Any:
    deboxed_arg = arg
    if isinstance(deboxed_arg, Asset):
        deboxed_arg = arg.mock
    if hasattr(deboxed_arg, "syft_action_data"):
        deboxed_arg = deboxed_arg.syft_action_data
    return deboxed_arg


def syft_function(
    input_policy: Union[InputPolicy, UID],
    output_policy: Union[OutputPolicy, UID],
) -> SubmitUserCode:
    if isinstance(input_policy, CustomInputPolicy):
        input_policy_type = SubmitUserPolicy.from_obj(input_policy)
    else:
        input_policy_type = type(input_policy)

    if isinstance(output_policy, CustomOutputPolicy):
        output_policy_type = SubmitUserPolicy.from_obj(output_policy)
    else:
        output_policy_type = type(output_policy)

    def decorator(f):
        x = SubmitUserCode(
            code=inspect.getsource(f),
            func_name=f.__name__,
            signature=inspect.signature(f),
            input_policy_type=input_policy_type,
            input_policy_init_kwargs=input_policy.init_kwargs,
            output_policy_type=output_policy_type,
            output_policy_init_kwargs=output_policy.init_kwargs,
            local_function=f,
            input_kwargs=f.__code__.co_varnames[: f.__code__.co_argcount],
        )
        print("submitusercode", x)
        return x

    return decorator


def generate_unique_func_name(context: TransformContext) -> TransformContext:
    code_hash = context.output["code_hash"]
    service_func_name = context.output["func_name"]
    context.output["service_func_name"] = service_func_name
    func_name = f"user_func_{service_func_name}_{context.credentials}_{code_hash}"
    user_unique_func_name = f"user_func_{service_func_name}_{context.credentials}"
    context.output["unique_func_name"] = func_name
    context.output["user_unique_func_name"] = user_unique_func_name
    return context


def process_code(
    raw_code: str,
    func_name: str,
    original_func_name: str,
    input_kwargs: List[str],  # Dict[str, Any],
    # outputs: List[str],
) -> str:
    tree = ast.parse(raw_code)

    # check there are no globals
    v = GlobalsVisitor()
    v.visit(tree)

    f = tree.body[0]
    f.decorator_list = []

    keywords = [
        ast.keyword(arg=i, value=[ast.Name(id=i)])
        # for _, inputs in input_kwargs.items()
        for i in input_kwargs
    ]
    call_stmt = ast.Assign(
        targets=[ast.Name(id="result")],
        value=ast.Call(
            func=ast.Name(id=original_func_name), args=[], keywords=keywords
        ),
        lineno=0,
    )

    # if len(outputs) > 0:
    #     output_list = ast.List(elts=[ast.Constant(value=x) for x in outputs])
    #     return_stmt = ast.Return(
    #         value=ast.DictComp(
    #             key=ast.Name(id="k"),
    #             value=ast.Subscript(
    #                 value=ast.Name(id="result"),
    #                 slice=ast.Name(id="k"),
    #             ),
    #             generators=[
    #                 ast.comprehension(
    #                     target=ast.Name(id="k"), iter=output_list, ifs=[], is_async=0
    #                 )
    #             ],
    #         )
    #     )
    #     # requires typing module imported but main code returned is FunctionDef not Module
    #     # return_annotation = ast.parse("typing.Dict[str, typing.Any]", mode="eval").body
    # else:
    return_stmt = ast.Return(value=ast.Name(id="result"))
    # requires typing module imported but main code returned is FunctionDef not Module
    # return_annotation = ast.parse("typing.Any", mode="eval").body

    new_body = tree.body + [call_stmt, return_stmt]

    wrapper_function = ast.FunctionDef(
        name=func_name,
        args=f.args,
        body=new_body,
        decorator_list=[],
        returns=None,
        lineno=0,
    )

    return unparse(wrapper_function)


def new_check_code(context: TransformContext) -> TransformContext:
    try:
        print("checking new code", context.output["raw_code"])
        processed_code = process_code(
            raw_code=context.output["raw_code"],
            func_name=context.output["unique_func_name"],
            original_func_name=context.output["service_func_name"],
            input_kwargs=list(context.output["input_policy_init_kwargs"].keys()),
            # outputs=context.output["outputs"], # handled by output policy
        )
        context.output["parsed_code"] = processed_code

    except Exception as e:
        print("exception as", e)
        raise e

    return context


def compile_byte_code(parsed_code: str) -> Optional[PyCodeObject]:
    try:
        return compile(parsed_code, "<string>", "exec")
    except Exception as e:
        print("WARNING: to compile byte code", e)
    return None


def compile_code(context: TransformContext) -> TransformContext:
    byte_code = compile_byte_code(context.output["parsed_code"])
    if byte_code is None:
        raise Exception(
            "Unable to compile byte code from parsed code. "
            + context.output["parsed_code"]
        )
    return context


def hash_code(context: TransformContext) -> TransformContext:
    code = context.output["code"]
    del context.output["code"]
    context.output["raw_code"] = code
    code_hash = hashlib.sha256(code.encode("utf8")).hexdigest()
    context.output["code_hash"] = code_hash
    return context


def add_credentials_for_key(key: str) -> Callable:
    def add_credentials(context: TransformContext) -> TransformContext:
        context.output[key] = context.credentials
        return context

    return add_credentials


# def modify_signature(context: TransformContext) -> TransformContext:
#     sig = context.output["signature"]
#     context.output["signature"] = sig.replace(return_annotation=Dict[str, Any])
#     return context


# def init_input_policy_state(context: TransformContext) -> TransformContext:
#     print("cant init until approved?")
#     # context.output["input_policy"] =
#     return context


# def init_output_policy_state(context: TransformContext) -> TransformContext:
#     # context.output["output_policy"] =
#     return context


def check_policy(policy: Policy, context: TransformContext) -> TransformContext:
    policy_service = context.node.get_service(PolicyService)
    if isinstance(policy, SubmitUserPolicy):
        policy = policy.to(UserPolicy, context=context)
    elif isinstance(policy, UID):
        policy = policy_service.get_policy_by_uid(context, policy)
        if policy.is_ok():
            policy = policy.ok()

    # provide node context for method operations until we finish LinkedObjects
    # policy.node_uid = context.node.id
    return policy


def check_input_policy(context: TransformContext) -> TransformContext:
    print("checking input policy")
    ip = context.output["input_policy_type"]
    ip = check_policy(policy=ip, context=context)
    # ip.node_uid = context.node.id
    context.output["input_policy_type"] = ip
    print("finished checking input policy")
    return context


def check_output_policy(context: TransformContext) -> TransformContext:
    print("checking output policy")
    op = context.output["output_policy_type"]
    op = check_policy(policy=op, context=context)
    # op.node_uid = context.node.id
    context.output["output_policy_type"] = op
    print("finished checking output policy")
    return context


def add_custom_status(context: TransformContext) -> TransformContext:
    print("adding custom status")
    input_keys = list(context.output["input_policy_init_kwargs"].keys())
    print("input_keys", input_keys)
    if context.node.node_type == NodeType.DOMAIN:
        print("1")
        node_view = NodeView(
            node_name=context.node.name, verify_key=context.node.signing_key.verify_key
        )
        print("2", node_view)
        print("input_keys", input_keys)
        if node_view in input_keys:
            p = UserCodeStatusContext(base_dict={node_view: UserCodeStatus.SUBMITTED})
            print("p", p)
            context.output["status"] = UserCodeStatusContext(
                base_dict={node_view: UserCodeStatus.SUBMITTED}
            )
        else:
            print("not implemented?")
            raise NotImplementedError
    elif context.node.node_type == NodeType.ENCLAVE:
        print("3")
        base_dict = {key: UserCodeStatus.SUBMITTED for key in input_keys}

        context.output["status"] = UserCodeStatusContext(base_dict=base_dict)
        print("4")
    else:
        # Consult with Madhava, on propogating errors from transforms
        raise NotImplementedError
    print("finished adding custom status")
    return context


@transform(SubmitUserCode, UserCode)
def submit_user_code_to_user_code() -> List[Callable]:
    return [
        generate_id,
        hash_code,
        generate_unique_func_name,
        # modify_signature,
        check_input_policy,
        check_output_policy,
        # init_input_policy_state,
        # init_output_policy_state,
        new_check_code,
        # compile_code, # don't compile code till its approved
        add_credentials_for_key("user_verify_key"),
        add_custom_status,
    ]


@serializable()
class UserCodeExecutionResult(SyftObject):
    # version
    __canonical_name__ = "UserCodeExecutionResult"
    __version__ = SYFT_OBJECT_VERSION_1

    id: UID
    user_code_id: UID
    stdout: str
    stderr: str
    result: Any


def execute_byte_code(code_item: UserCode, kwargs: Dict[str, Any]) -> Any:
    stdout_ = sys.stdout
    stderr_ = sys.stderr

    try:
        stdout = StringIO()
        stderr = StringIO()

        sys.stdout = stdout
        sys.stderr = stderr

        # statisfy lint checker
        result = None

        exec(code_item.byte_code)  # nosec

        evil_string = f"{code_item.unique_func_name}(**kwargs)"
        result = eval(evil_string, None, locals())  # nosec

        # restore stdout and stderr
        sys.stdout = stdout_
        sys.stderr = stderr_

        return UserCodeExecutionResult(
            user_code_id=code_item.id,
            stdout=str(stdout.getvalue()),
            stderr=str(stderr.getvalue()),
            result=result,
        )

    except Exception as e:
        print("execute_byte_code failed", e, file=stderr_)
    finally:
        sys.stdout = stdout_
        sys.stderr = stderr_
