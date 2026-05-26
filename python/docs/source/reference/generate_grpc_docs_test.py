import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from generate_grpc_docs import (
    PROTO_DIRS,
    EnumDef,
    EnumValue,
    Field,
    Message,
    ProtoFile,
    RpcMethod,
    Service,
    _extract_inline_comment,
    _find_brace_end,
    _parse_enum_block,
    _parse_message_block,
    _parse_nested_message,
    _parse_oneof_block,
    _parse_service_block,
    main,
    parse_proto_file,
    render_enum,
    render_message,
    render_proto_doc,
    render_service,
)

MINIMAL_PROTO = """\
syntax = "proto3";

package test.v1;
"""

PROTO_WITH_ENUM = """\
syntax = "proto3";

package test.v1;

// Status of the widget
enum WidgetStatus {
  WIDGET_STATUS_UNSPECIFIED = 0; // Unknown status
  WIDGET_STATUS_ACTIVE = 1;     // Widget is active
  WIDGET_STATUS_INACTIVE = 2;   // Widget is inactive
}
"""

PROTO_WITH_MESSAGE = """\
syntax = "proto3";

package test.v1;

// A request to create a widget
message CreateWidgetRequest {
  string name = 1;              // The widget name
  int32 count = 2;              // How many to create
  optional string description = 3; // Optional description
  repeated string tags = 4;     // List of tags
  map<string, string> labels = 5; // Key-value labels
}
"""

PROTO_WITH_SERVICE = """\
syntax = "proto3";

package test.v1;

import "google/protobuf/empty.proto";

// A service for managing widgets
service WidgetService {
  // Create a new widget
  rpc CreateWidget(CreateWidgetRequest) returns (CreateWidgetResponse);
  // List all widgets with streaming
  rpc ListWidgets(ListWidgetsRequest) returns (stream ListWidgetsResponse);
  // Upload widget data
  rpc UploadData(stream UploadDataRequest) returns (UploadDataResponse);
  // Bidirectional widget sync
  rpc SyncWidgets(stream SyncRequest) returns (stream SyncResponse);
}
"""

PROTO_WITH_NESTED_MESSAGE = """\
syntax = "proto3";

package test.v1;

// Outer message
message Outer {
  // Inner message
  message Inner {
    string value = 1; // The value
  }
  Inner nested = 1; // A nested field
}
"""

PROTO_WITH_ONEOF = """\
syntax = "proto3";

package test.v1;

message FlexibleRequest {
  oneof target {
    string name = 1;
    int32 id = 2;
  }
}
"""


class TestFindBraceEnd:
    def test_ignores_unbalanced_brace_in_comment(self):
        lines = [
            'message Foo { // extra {',
            '  string value = 1;',
            '}',
            '',
            'message Bar {',
            '  string x = 1;',
            '}',
        ]
        result = _find_brace_end(lines, 0)
        assert result == 3

    def test_ignores_unbalanced_brace_in_string(self):
        lines = [
            'message Foo {',
            '  string fmt = 1; // format: "use {name"',
            '}',
            '',
            'message Bar {',
            '  string x = 1;',
            '}',
        ]
        result = _find_brace_end(lines, 0)
        assert result == 3


class TestExtractInlineComment:
    def test_extracts_comment_after_semicolon(self):
        result = _extract_inline_comment("  string name = 1; // The name")
        assert result == "The name"

    def test_extracts_comment_without_semicolon(self):
        result = _extract_inline_comment("// standalone comment")
        assert result == "standalone comment"

    def test_returns_empty_for_no_comment(self):
        result = _extract_inline_comment("  string name = 1;")
        assert result == ""

    def test_returns_empty_for_blank_line(self):
        result = _extract_inline_comment("")
        assert result == ""

    def test_extracts_comment_with_extra_whitespace(self):
        result = _extract_inline_comment("  int32 id = 2; //   spaced out  ")
        assert result == "spaced out"


class TestParseProtoFileBasic:
    def test_parses_package_name(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(MINIMAL_PROTO, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        assert result["package"] == "test.v1"

    def test_parses_syntax(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(MINIMAL_PROTO, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        assert result["syntax"] == "proto3"

    def test_parses_filename(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(MINIMAL_PROTO, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        assert result["filename"] == "test.proto"

    def test_empty_proto_has_no_services(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(MINIMAL_PROTO, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        assert result["services"] == []

    def test_empty_proto_has_no_messages(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(MINIMAL_PROTO, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        assert result["messages"] == []

    def test_empty_proto_has_no_enums(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(MINIMAL_PROTO, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        assert result["enums"] == []


class TestParseProtoFileEnums:
    def test_parses_enum_name(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_ENUM, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        assert len(result["enums"]) == 1
        assert result["enums"][0]["name"] == "WidgetStatus"

    def test_parses_enum_description(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_ENUM, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        assert result["enums"][0]["description"] == "Status of the widget"

    def test_parses_enum_values(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_ENUM, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        values = result["enums"][0]["values"]
        assert len(values) == 3
        assert values[0]["name"] == "WIDGET_STATUS_UNSPECIFIED"
        assert values[0]["number"] == 0
        assert values[0]["description"] == "Unknown status"
        assert values[1]["name"] == "WIDGET_STATUS_ACTIVE"
        assert values[1]["number"] == 1
        assert values[1]["description"] == "Widget is active"

    def test_parses_hex_enum_values(self, tmp_path):
        proto_content = """\
syntax = "proto3";

package test.v1;

enum FrameType {
  FRAME_TYPE_DATA = 0x00;
  FRAME_TYPE_RST_STREAM = 0x03;
  FRAME_TYPE_PING = 0x06;
}
"""
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(proto_content, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        values = result["enums"][0]["values"]
        assert len(values) == 3
        assert values[0]["name"] == "FRAME_TYPE_DATA"
        assert values[0]["number"] == 0
        assert values[1]["name"] == "FRAME_TYPE_RST_STREAM"
        assert values[1]["number"] == 3
        assert values[2]["name"] == "FRAME_TYPE_PING"
        assert values[2]["number"] == 6


class TestParseProtoFileMessages:
    def test_parses_message_name(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_MESSAGE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        assert len(result["messages"]) == 1
        assert result["messages"][0]["name"] == "CreateWidgetRequest"

    def test_parses_message_description(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_MESSAGE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        assert result["messages"][0]["description"] == "A request to create a widget"

    def test_parses_message_fields(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_MESSAGE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        fields = result["messages"][0]["fields"]
        assert len(fields) == 5

    def test_parses_simple_field(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_MESSAGE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        fields = result["messages"][0]["fields"]
        name_field = fields[0]
        assert name_field["name"] == "name"
        assert name_field["number"] == 1
        assert name_field["type"] == "string"
        assert name_field["label"] == ""
        assert name_field["description"] == "The widget name"

    def test_parses_optional_field(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_MESSAGE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        fields = result["messages"][0]["fields"]
        desc_field = fields[2]
        assert desc_field["name"] == "description"
        assert desc_field["label"] == "optional"

    def test_parses_repeated_field(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_MESSAGE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        fields = result["messages"][0]["fields"]
        tags_field = fields[3]
        assert tags_field["name"] == "tags"
        assert tags_field["label"] == "repeated"

    def test_parses_map_field(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_MESSAGE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        fields = result["messages"][0]["fields"]
        labels_field = fields[4]
        assert labels_field["name"] == "labels"
        assert labels_field["type"] == "map<string, string>"


class TestParseProtoFileServices:
    def test_parses_service_name(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_SERVICE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        assert len(result["services"]) == 1
        assert result["services"][0]["name"] == "WidgetService"

    def test_parses_service_description(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_SERVICE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        assert result["services"][0]["description"] == "A service for managing widgets"

    def test_parses_rpc_methods(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_SERVICE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        methods = result["services"][0]["methods"]
        assert len(methods) == 4

    def test_parses_unary_rpc(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_SERVICE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        methods = result["services"][0]["methods"]
        create = methods[0]
        assert create["name"] == "CreateWidget"
        assert create["input_type"] == "CreateWidgetRequest"
        assert create["output_type"] == "CreateWidgetResponse"
        assert create["client_streaming"] is False
        assert create["server_streaming"] is False
        assert create["description"] == "Create a new widget"

    def test_parses_server_streaming_rpc(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_SERVICE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        methods = result["services"][0]["methods"]
        list_rpc = methods[1]
        assert list_rpc["name"] == "ListWidgets"
        assert list_rpc["server_streaming"] is True
        assert list_rpc["client_streaming"] is False

    def test_parses_client_streaming_rpc(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_SERVICE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        methods = result["services"][0]["methods"]
        upload = methods[2]
        assert upload["name"] == "UploadData"
        assert upload["client_streaming"] is True
        assert upload["server_streaming"] is False

    def test_parses_bidi_streaming_rpc(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_SERVICE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        methods = result["services"][0]["methods"]
        sync = methods[3]
        assert sync["name"] == "SyncWidgets"
        assert sync["client_streaming"] is True
        assert sync["server_streaming"] is True


class TestParseProtoFileNested:
    def test_parses_nested_messages(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_NESTED_MESSAGE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        assert len(result["messages"]) == 2
        names = [m["name"] for m in result["messages"]]
        assert "Outer" in names
        assert "Outer.Inner" in names


PROTO_WITH_EMPTY_MESSAGE = """\
syntax = "proto3";

package test.v1;

message EmptyMsg {}

message NextMsg {
  string value = 1;
}
"""


class TestParseProtoFileEmptyBraces:
    def test_empty_brace_message_does_not_nest_siblings(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_EMPTY_MESSAGE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        names = [m["name"] for m in result["messages"]]
        assert "EmptyMsg" in names
        assert "NextMsg" in names
        assert len(result["messages"]) == 2

    def test_empty_brace_message_has_no_fields(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_EMPTY_MESSAGE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        empty = next(m for m in result["messages"] if m["name"] == "EmptyMsg")
        assert empty["fields"] == []

    def test_sibling_after_empty_has_correct_fields(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_EMPTY_MESSAGE, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        next_msg = next(m for m in result["messages"] if m["name"] == "NextMsg")
        assert len(next_msg["fields"]) == 1
        assert next_msg["fields"][0]["name"] == "value"


class TestParseProtoFileOneof:
    def test_parses_oneof_fields(self, tmp_path):
        proto_file = tmp_path / "test.proto"
        proto_file.write_text(PROTO_WITH_ONEOF, encoding="utf-8")
        result = parse_proto_file(str(proto_file))
        fields = result["messages"][0]["fields"]
        assert len(fields) == 2
        assert fields[0]["name"] == "name"
        assert fields[0]["label"] == "oneof target"
        assert fields[1]["name"] == "id"
        assert fields[1]["label"] == "oneof target"


class TestRenderService:
    def test_renders_service_heading(self):
        service = Service(
            name="TestService",
            description="A test service",
            methods=[],
        )
        result = render_service(service)
        assert "### TestService" in result

    def test_renders_service_description(self):
        service = Service(
            name="TestService",
            description="A test service",
            methods=[],
        )
        result = render_service(service)
        assert "A test service" in result

    def test_renders_method_table(self):
        service = Service(
            name="TestService",
            description="",
            methods=[
                RpcMethod(
                    name="DoSomething",
                    input_type="DoRequest",
                    output_type="DoResponse",
                    client_streaming=False,
                    server_streaming=False,
                    description="Does something",
                ),
            ],
        )
        result = render_service(service)
        assert "| Method | Request | Response | Description |" in result
        assert "| DoSomething | DoRequest | DoResponse | Does something |" in result

    def test_renders_streaming_prefix(self):
        service = Service(
            name="TestService",
            description="",
            methods=[
                RpcMethod(
                    name="StreamMethod",
                    input_type="StreamReq",
                    output_type="StreamResp",
                    client_streaming=True,
                    server_streaming=True,
                    description="Streams",
                ),
            ],
        )
        result = render_service(service)
        assert "stream StreamReq" in result
        assert "stream StreamResp" in result

    def test_escapes_pipe_in_description(self):
        service = Service(
            name="TestService",
            description="",
            methods=[
                RpcMethod(
                    name="M",
                    input_type="A",
                    output_type="B",
                    client_streaming=False,
                    server_streaming=False,
                    description="foo | bar",
                ),
            ],
        )
        result = render_service(service)
        assert r"foo \| bar" in result


class TestRenderMessage:
    def test_renders_message_heading(self):
        message = Message(name="TestMsg", description="A test message", fields=[])
        result = render_message(message)
        assert "### TestMsg" in result

    def test_renders_message_description(self):
        message = Message(name="TestMsg", description="A test message", fields=[])
        result = render_message(message)
        assert "A test message" in result

    def test_renders_no_fields_message_when_empty(self):
        message = Message(name="TestMsg", description="", fields=[])
        result = render_message(message)
        assert "*No fields defined.*" in result

    def test_renders_field_table(self):
        message = Message(
            name="TestMsg",
            description="",
            fields=[
                Field(
                    name="id",
                    number=1,
                    type="string",
                    label="",
                    description="The ID",
                ),
            ],
        )
        result = render_message(message)
        assert "| Field | Number | Type | Label | Description |" in result
        assert "| id | 1 | string |  | The ID |" in result

    def test_renders_field_with_label(self):
        message = Message(
            name="TestMsg",
            description="",
            fields=[
                Field(
                    name="items",
                    number=1,
                    type="string",
                    label="repeated",
                    description="List of items",
                ),
            ],
        )
        result = render_message(message)
        assert "| items | 1 | string | repeated | List of items |" in result


class TestRenderEnum:
    def test_renders_enum_heading(self):
        enum_def = EnumDef(name="TestEnum", description="A test enum", values=[])
        result = render_enum(enum_def)
        assert "### TestEnum" in result

    def test_renders_enum_description(self):
        enum_def = EnumDef(name="TestEnum", description="A test enum", values=[])
        result = render_enum(enum_def)
        assert "A test enum" in result

    def test_renders_value_table(self):
        enum_def = EnumDef(
            name="TestEnum",
            description="",
            values=[
                EnumValue(name="VALUE_A", number=0, description="First"),
                EnumValue(name="VALUE_B", number=1, description="Second"),
            ],
        )
        result = render_enum(enum_def)
        assert "| Name | Number | Description |" in result
        assert "| VALUE_A | 0 | First |" in result
        assert "| VALUE_B | 1 | Second |" in result


class TestRenderProtoDoc:
    def test_renders_filename_as_title(self):
        proto_data = ProtoFile(
            filename="widget.proto",
            package="test.v1",
            syntax="proto3",
            services=[],
            messages=[],
            enums=[],
        )
        result = render_proto_doc(proto_data)
        assert "# widget\n" in result

    def test_renders_package_name(self):
        proto_data = ProtoFile(
            filename="widget.proto",
            package="test.v1",
            syntax="proto3",
            services=[],
            messages=[],
            enums=[],
        )
        result = render_proto_doc(proto_data)
        assert "`test.v1`" in result

    def test_renders_services_section_when_present(self):
        proto_data = ProtoFile(
            filename="widget.proto",
            package="test.v1",
            syntax="proto3",
            services=[
                Service(name="Svc", description="", methods=[]),
            ],
            messages=[],
            enums=[],
        )
        result = render_proto_doc(proto_data)
        assert "## Services" in result

    def test_omits_services_section_when_empty(self):
        proto_data = ProtoFile(
            filename="widget.proto",
            package="test.v1",
            syntax="proto3",
            services=[],
            messages=[],
            enums=[],
        )
        result = render_proto_doc(proto_data)
        assert "## Services" not in result

    def test_renders_messages_section_when_present(self):
        proto_data = ProtoFile(
            filename="widget.proto",
            package="test.v1",
            syntax="proto3",
            services=[],
            messages=[
                Message(name="Msg", description="", fields=[]),
            ],
            enums=[],
        )
        result = render_proto_doc(proto_data)
        assert "## Messages" in result

    def test_renders_enums_section_when_present(self):
        proto_data = ProtoFile(
            filename="widget.proto",
            package="test.v1",
            syntax="proto3",
            services=[],
            messages=[],
            enums=[
                EnumDef(name="E", description="", values=[]),
            ],
        )
        result = render_proto_doc(proto_data)
        assert "## Enums" in result


class TestMain:
    def test_exits_with_error_when_no_protos_found(self, tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            main(proto_dirs=[str(tmp_path)])
        assert exc_info.value.code == 1

    def test_generates_output_files(self, tmp_path):
        proto_dir = tmp_path / "protos"
        proto_dir.mkdir()
        output_dir = tmp_path / "output"

        proto_content = PROTO_WITH_ENUM
        (proto_dir / "test.proto").write_text(proto_content, encoding="utf-8")

        main(proto_dirs=[str(proto_dir)], output_dir=str(output_dir))

        generated = sorted(f.name for f in output_dir.iterdir())
        assert "test.md" in generated
        assert "index.md" in generated

    def test_generated_content_contains_enum(self, tmp_path):
        proto_dir = tmp_path / "protos"
        proto_dir.mkdir()
        output_dir = tmp_path / "output"

        (proto_dir / "test.proto").write_text(PROTO_WITH_ENUM, encoding="utf-8")

        main(proto_dirs=[str(proto_dir)], output_dir=str(output_dir))

        content = (output_dir / "test.md").read_text(encoding="utf-8")
        assert "WidgetStatus" in content

    def test_index_contains_toctree(self, tmp_path):
        proto_dir = tmp_path / "protos"
        proto_dir.mkdir()
        output_dir = tmp_path / "output"

        (proto_dir / "test.proto").write_text(MINIMAL_PROTO, encoding="utf-8")

        main(proto_dirs=[str(proto_dir)], output_dir=str(output_dir))

        index_content = (output_dir / "index.md").read_text(encoding="utf-8")
        assert "```{toctree}" in index_content
        assert "test.md" in index_content

    def test_index_contains_link_to_doc(self, tmp_path):
        proto_dir = tmp_path / "protos"
        proto_dir.mkdir()
        output_dir = tmp_path / "output"

        (proto_dir / "test.proto").write_text(MINIMAL_PROTO, encoding="utf-8")

        main(proto_dirs=[str(proto_dir)], output_dir=str(output_dir))

        index_content = (output_dir / "index.md").read_text(encoding="utf-8")
        assert "[test](test.md)" in index_content

    def test_processes_multiple_directories(self, tmp_path):
        dir1 = tmp_path / "dir1"
        dir1.mkdir()
        dir2 = tmp_path / "dir2"
        dir2.mkdir()
        output_dir = tmp_path / "output"

        (dir1 / "a.proto").write_text(MINIMAL_PROTO, encoding="utf-8")
        (dir2 / "b.proto").write_text(MINIMAL_PROTO, encoding="utf-8")

        main(proto_dirs=[str(dir1), str(dir2)], output_dir=str(output_dir))

        generated = sorted(f.name for f in output_dir.iterdir())
        assert "a.md" in generated
        assert "b.md" in generated

    def test_handles_malformed_proto_file_gracefully(self, tmp_path):
        proto_dir = tmp_path / "protos"
        proto_dir.mkdir()
        output_dir = tmp_path / "output"

        malformed_content = "this is not valid proto content\n{{{}\nrandom text\n"
        (proto_dir / "malformed.proto").write_text(malformed_content, encoding="utf-8")

        main(proto_dirs=[str(proto_dir)], output_dir=str(output_dir))

        generated = sorted(f.name for f in output_dir.iterdir())
        assert "malformed.md" in generated
        assert "index.md" in generated

    def test_malformed_proto_produces_valid_markdown(self, tmp_path):
        proto_dir = tmp_path / "protos"
        proto_dir.mkdir()
        output_dir = tmp_path / "output"

        malformed_content = "garbage content with no proto definitions\n"
        (proto_dir / "garbage.proto").write_text(malformed_content, encoding="utf-8")

        main(proto_dirs=[str(proto_dir)], output_dir=str(output_dir))

        content = (output_dir / "garbage.md").read_text(encoding="utf-8")
        assert "# garbage" in content


class TestParseNestedMessageGuard:
    def test_returns_empty_when_line_does_not_match_message(self):
        lines = ["not a message line"]
        next_index, messages = _parse_nested_message(lines, 0, "Parent")
        assert next_index == 1
        assert messages == []

    def test_returns_empty_for_blank_line(self):
        lines = [""]
        next_index, messages = _parse_nested_message(lines, 0, "")
        assert next_index == 1
        assert messages == []


class TestParseOneofBlockGuard:
    def test_returns_empty_when_line_does_not_match_oneof(self):
        lines = ["not a oneof line"]
        next_index, fields = _parse_oneof_block(lines, 0)
        assert next_index == 1
        assert fields == []

    def test_returns_empty_for_blank_line(self):
        lines = [""]
        next_index, fields = _parse_oneof_block(lines, 0)
        assert next_index == 1
        assert fields == []


class TestParseServiceBlockGuard:
    def test_raises_value_error_when_line_does_not_match_service(self):
        lines = ["not a service line"]
        with pytest.raises(ValueError, match="Expected service declaration"):
            _parse_service_block(lines, 0)

    def test_raises_value_error_for_blank_line(self):
        lines = [""]
        with pytest.raises(ValueError, match="Expected service declaration"):
            _parse_service_block(lines, 0)


class TestParseMessageBlockGuard:
    def test_raises_value_error_when_line_does_not_match_message(self):
        lines = ["not a message line"]
        with pytest.raises(ValueError, match="Expected message declaration"):
            _parse_message_block(lines, 0)

    def test_raises_value_error_for_blank_line(self):
        lines = [""]
        with pytest.raises(ValueError, match="Expected message declaration"):
            _parse_message_block(lines, 0)


class TestParseEnumBlockGuard:
    def test_raises_value_error_when_line_does_not_match_enum(self):
        lines = ["not an enum line"]
        with pytest.raises(ValueError, match="Expected enum declaration"):
            _parse_enum_block(lines, 0)

    def test_raises_value_error_for_blank_line(self):
        lines = [""]
        with pytest.raises(ValueError, match="Expected enum declaration"):
            _parse_enum_block(lines, 0)


class TestMainEntryPoint:
    def test_script_runs_as_main_module(self, tmp_path):
        import subprocess

        all_exist = all(os.path.isdir(d) for d in PROTO_DIRS)
        if not all_exist:
            pytest.skip("Default PROTO_DIRS not available in this environment")

        script_path = os.path.join(os.path.dirname(__file__), "generate_grpc_docs.py")
        env = os.environ.copy()
        env.pop("COV_CORE_DATAFILE", None)
        env.pop("COV_CORE_SOURCE", None)
        env.pop("COV_CORE_CONFIG", None)
        env["COVERAGE_PROCESS_START"] = ""
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(__file__),
            env=env,
        )
        assert result.returncode == 0
        assert "Generated" in result.stdout


class TestMainIntegration:
    def test_generates_docs_from_default_proto_dirs(self, tmp_path):
        all_exist = all(os.path.isdir(d) for d in PROTO_DIRS)
        if not all_exist:
            pytest.skip("Default PROTO_DIRS not available in this environment")

        output_dir = tmp_path / "output"
        main(output_dir=str(output_dir))

        generated = sorted(f.name for f in output_dir.iterdir())
        assert "index.md" in generated
        assert len(generated) > 1
