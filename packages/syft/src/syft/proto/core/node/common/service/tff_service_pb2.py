# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: proto/core/node/common/service/tff_service.proto
"""Generated protocol buffer code."""
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import message as _message
from google.protobuf import reflection as _reflection
from google.protobuf import symbol_database as _symbol_database

# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()


from syft.proto.core.common import (
    common_object_pb2 as proto_dot_core_dot_common_dot_common__object__pb2,
)
from syft.proto.core.io import address_pb2 as proto_dot_core_dot_io_dot_address__pb2


DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(
    b'\n0proto/core/node/common/service/tff_service.proto\x12\x1dsyft.core.node.common.service\x1a%proto/core/common/common_object.proto\x1a\x1bproto/core/io/address.proto"\x95\x01\n\nTFFMessage\x12%\n\x06msg_id\x18\x01 \x01(\x0b\x32\x15.syft.core.common.UID\x12&\n\x07\x61\x64\x64ress\x18\x02 \x01(\x0b\x32\x15.syft.core.io.Address\x12\'\n\x08reply_to\x18\x03 \x01(\x0b\x32\x15.syft.core.io.Address\x12\x0f\n\x07payload\x18\x04 \x01(\x0c"q\n\x0fTFFReplyMessage\x12%\n\x06msg_id\x18\x01 \x01(\x0b\x32\x15.syft.core.common.UID\x12&\n\x07\x61\x64\x64ress\x18\x02 \x01(\x0b\x32\x15.syft.core.io.Address\x12\x0f\n\x07payload\x18\x03 \x01(\x0c\x62\x06proto3'
)


_TFFMESSAGE = DESCRIPTOR.message_types_by_name["TFFMessage"]
_TFFREPLYMESSAGE = DESCRIPTOR.message_types_by_name["TFFReplyMessage"]
TFFMessage = _reflection.GeneratedProtocolMessageType(
    "TFFMessage",
    (_message.Message,),
    {
        "DESCRIPTOR": _TFFMESSAGE,
        "__module__": "proto.core.node.common.service.tff_service_pb2"
        # @@protoc_insertion_point(class_scope:syft.core.node.common.service.TFFMessage)
    },
)
_sym_db.RegisterMessage(TFFMessage)

TFFReplyMessage = _reflection.GeneratedProtocolMessageType(
    "TFFReplyMessage",
    (_message.Message,),
    {
        "DESCRIPTOR": _TFFREPLYMESSAGE,
        "__module__": "proto.core.node.common.service.tff_service_pb2"
        # @@protoc_insertion_point(class_scope:syft.core.node.common.service.TFFReplyMessage)
    },
)
_sym_db.RegisterMessage(TFFReplyMessage)

if _descriptor._USE_C_DESCRIPTORS == False:

    DESCRIPTOR._options = None
    _TFFMESSAGE._serialized_start = 152
    _TFFMESSAGE._serialized_end = 301
    _TFFREPLYMESSAGE._serialized_start = 303
    _TFFREPLYMESSAGE._serialized_end = 416
# @@protoc_insertion_point(module_scope)
