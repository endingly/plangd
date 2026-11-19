import json
import subprocess
import sys


def exchange(executable, messages):
    """Run one stdio session and decode all its LSP responses."""
    frames = []
    for message in messages:
        body = json.dumps({"jsonrpc": "2.0", **message}).encode("utf-8")
        frames.append(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)

    process = subprocess.run(
        [executable], input=b"".join(frames), capture_output=True, timeout=5
    )
    responses = []
    output = process.stdout
    while output:
        header, separator, body = output.partition(b"\r\n\r\n")
        assert separator, output
        length = next(
            int(line.split(b":", 1)[1])
            for line in header.split(b"\r\n")
            if line.lower().startswith(b"content-length:")
        )
        responses.append(json.loads(body[:length]))
        output = body[length:]
    return process, responses


def check_document_sync(executable, initialize, shutdown):
    """Check diagnostics against the editor buffer, its version and its URI."""
    uri = "file:///tmp/plangd-unsaved.ptx"
    prefix = "  /* 中文🚀 */ mov.u32 %r1, "
    invalid = "\r\n".join(
        [
            ".version 8.0",
            ".target sm_90",
            ".address_size 64",
            ".visible .entry demo() {",
            prefix + ";",
            "  ret;",
            "}",
            "",
        ]
    )
    valid = invalid.replace(prefix + ";", prefix + "0;")

    def change(version, *texts):
        return {
            "method": "textDocument/didChange",
            "params": {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": text} for text in texts],
            },
        }

    process, responses = exchange(
        executable,
        [
            *initialize,
            {
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": uri,
                        "languageId": "ptx",
                        "version": 1,
                        "text": invalid,
                    },
                },
            },
            change(2, invalid, valid),
            change(1, invalid),  # A stale change must leave version 2 intact.
            change(3, invalid),
            {
                "method": "textDocument/didClose",
                "params": {"textDocument": {"uri": uri}},
            },
            change(4, invalid),  # Closed documents no longer receive diagnostics.
            *shutdown,
        ],
    )
    assert process.returncode == 0, process.stderr
    assert len(responses) == 6, responses
    notifications = responses[1:-1]
    assert all(
        event.get("method") == "textDocument/publishDiagnostics"
        for event in notifications
    ), responses
    opened, fixed, edited, closed = [event["params"] for event in notifications]
    assert all(
        item["uri"] == uri for item in (opened, fixed, edited, closed)
    ), responses
    assert [opened["version"], fixed["version"], edited["version"]] == [1, 2, 3]

    column = len(prefix.encode("utf-16-le")) // 2
    diagnostic = opened["diagnostics"][0]
    assert diagnostic["range"] == {
        "start": {"line": 4, "character": column},
        "end": {"line": 4, "character": column + 1},
    }, diagnostic
    assert diagnostic["severity"] == 1 and diagnostic["source"] == "plangd"
    assert fixed["diagnostics"] == [], fixed
    assert edited["diagnostics"] == opened["diagnostics"], edited
    assert closed["diagnostics"] == [], closed


def check_symbol_navigation(executable, initialize, shutdown):
    """Check symbol navigation and instruction hover across document versions."""
    uri = "file:///tmp/plangd-definition.ptx"
    comment = "/* 中文🚀 mov */"
    lines = [
        ".version 8.0",
        ".target sm_90",
        ".address_size 64",
        ".visible .entry first() {",
        "  .reg .u32 %r1;",
        "  mov.u32 %r1, 1;",
        "  bra done;",
        "done:",
        "  ret;",
        "}",
        ".visible .entry second() {",
        f"  {comment} .reg .s32 %r1;",
        f"  {comment} mov.s32 %r1, 2;",
        "  bra done;",
        "  mov.u32 %missing, 2;",
        "done:",
        "  .reg .u32 %tmp<4>;",
        "  mov.u32 %tmp1, 0;",
        "  {",
        "    add.u32 %tmp1, %tmp1, 1;",
        "  }",
        "  ret;",
        "}",
    ]

    def position(line, symbol, shift=0):
        prefix = lines[line][:lines[line].index(symbol)]
        column = len(prefix.encode("utf-16-le")) // 2
        return {"line": line + shift, "character": column}

    def request(request_id, cursor, method="definition"):
        return {
            "id": request_id,
            "method": f"textDocument/{method}",
            "params": {"textDocument": {"uri": uri}, "position": cursor},
        }

    def symbol_range(line, symbol, shift=0):
        start = position(line, symbol, shift)
        end = {**start, "character": start["character"] + len(symbol)}
        return {"start": start, "end": end}

    def location(line, symbol, shift=0):
        return {"uri": uri, "range": symbol_range(line, symbol, shift)}

    def hover(line, symbol, declaration, shift=0, kind="plaintext"):
        return {
            "contents": {"kind": kind, "value": declaration},
            "range": symbol_range(line, symbol, shift),
        }

    text = "\r\n".join(lines)
    open_document = {
        "method": "textDocument/didOpen",
        "params": {
            "textDocument": {
                "uri": uri,
                "languageId": "ptx",
                "version": 1,
                "text": text,
            },
        },
    }
    edited_text = text.replace(".reg .s32 %r1;", ".reg .b32 %r1;").replace(
        "mov.s32 %r1, 2;", "add.s32 %r1, %r1, 2;"
    )
    process, responses = exchange(
        executable,
        [
            *initialize,
            request(10, position(5, "%r1")),  # The buffer is not open yet.
            request(20, position(5, "%r1"), "hover"),
            open_document,
            request(11, position(5, "%r1")),
            request(12, position(12, "%r1")),
            request(13, position(6, "done")),
            request(14, position(13, "done")),
            request(15, position(14, "%missing")),
            request(16, position(5, ",")),  # The end of a reference is exclusive.
            request(21, position(5, "%r1"), "hover"),
            request(22, position(12, "%r1"), "hover"),
            request(23, position(13, "done"), "hover"),
            request(24, position(14, "%missing"), "hover"),
            request(25, position(5, ","), "hover"),
            request(26, position(17, "%tmp1"), "hover"),
            request(29, position(12, "mov.s32"), "hover"),
            request(32, position(12, ".s32"), "hover"),
            request(33, position(19, "add.u32"), "hover"),
            request(34, position(12, "mov */"), "hover"),
            {
                "method": "textDocument/didChange",
                "params": {
                    "textDocument": {"uri": uri, "version": 2},
                    "contentChanges": [
                        {"text": "// unsaved edit\r\n" + edited_text + "\r\n.global .u32 ;"}
                    ],
                },
            },
            request(27, position(12, "%r1", shift=1), "hover"),
            request(17, position(12, "%r1", shift=1)),
            request(18, position(13, "done", shift=1)),
            request(35, position(12, "mov.s32", shift=1), "hover"),
            {
                "method": "textDocument/didClose",
                "params": {"textDocument": {"uri": uri}},
            },
            request(19, position(13, "done", shift=1)),
            request(28, position(13, "done", shift=1), "hover"),
            *shutdown,
        ],
    )
    assert process.returncode == 0, process.stderr
    edited_diagnostics = next(
        response["params"]["diagnostics"]
        for response in responses
        if response.get("method") == "textDocument/publishDiagnostics"
        and response["params"].get("version") == 2
    )
    assert edited_diagnostics, responses
    results = {response["id"]: response for response in responses if "id" in response}
    expected = {
        10: None,
        11: location(4, "%r1"),
        12: location(11, "%r1"),
        13: location(7, "done"),
        14: location(15, "done"),
        15: None,
        16: None,
        17: location(11, "%r1", shift=1),
        18: location(15, "done", shift=1),
        19: None,
        20: None,
        21: hover(5, "%r1", ".reg .u32 %r1;"),
        22: hover(12, "%r1", ".reg .s32 %r1;"),
        23: hover(13, "done", "done:"),
        24: None,
        25: None,
        26: hover(17, "%tmp1", ".reg .u32 %tmp<4>;"),
        27: hover(12, "%r1", ".reg .b32 %r1;", shift=1),
        28: None,
        34: None,
    }
    for request_id, result in expected.items():
        assert results[request_id].get("result", ...) == result, results[request_id]

    mov = results[29]["result"]
    assert mov["range"] == symbol_range(12, "mov.s32"), mov
    assert mov["contents"]["kind"] == "plaintext", mov
    assert mov["contents"]["value"].startswith("mov{.v4.u32"), mov
    assert "Move a scalar register" in mov["contents"]["value"], mov
    assert results[32]["result"] == mov, results[32]

    for request_id, span in [
        (33, symbol_range(19, "add.u32")),
        (35, symbol_range(12, "mov.s32", shift=1)),
    ]:
        add = results[request_id]["result"]
        assert add["range"] == span, add
        assert "Integer add." in add["contents"]["value"], add
        assert "Standard floating-point add." in add["contents"]["value"], add

    markdown_initialize = {
        **initialize[0],
        "params": {
            **initialize[0]["params"],
            "capabilities": {
                "textDocument": {"hover": {"contentFormat": ["markdown", "plaintext"]}},
            },
        },
    }
    process, responses = exchange(
        executable,
        [
            markdown_initialize,
            *initialize[1:],
            open_document,
            request(30, position(12, "%r1"), "hover"),
            request(31, position(12, "mov.s32"), "hover"),
            *shutdown,
        ],
    )
    assert process.returncode == 0, process.stderr
    markdown = next(response for response in responses if response.get("id") == 30)
    assert markdown.get("result") == hover(
        12, "%r1", "```ptx\n.reg .s32 %r1;\n```", kind="markdown"
    ), markdown
    markdown_mov = next(
        response["result"] for response in responses if response.get("id") == 31
    )
    assert markdown_mov["contents"]["kind"] == "markdown", markdown_mov
    value = markdown_mov["contents"]["value"]
    assert value.startswith("```ptx\nmov{"), markdown_mov
    assert "\n```\n\nMove a scalar register" in value, markdown_mov


if __name__ == "__main__":
    executable, version = sys.argv[1:]
    version_result = subprocess.run(
        [executable, "--version"], capture_output=True, text=True, check=True
    )
    assert version_result.stdout == f"plangd {version}\n", version_result

    initialize = [
        {
            "id": 1,
            "method": "initialize",
            "params": {"processId": None, "rootUri": None, "capabilities": {}},
        },
        {"method": "initialized", "params": {}},
    ]
    shutdown = [{"id": 2, "method": "shutdown"}, {"method": "exit"}]
    process, responses = exchange(executable, [*initialize, *shutdown])
    assert process.returncode == 0, process.stderr
    assert [response["id"] for response in responses] == [1, 2], responses
    capabilities = responses[0]["result"]["capabilities"]
    assert capabilities["positionEncoding"] == "utf-16", capabilities
    assert capabilities["definitionProvider"] is True, capabilities
    assert capabilities["hoverProvider"] is True, capabilities
    assert capabilities["textDocumentSync"] == {
        "openClose": True, "change": 1,
    }, capabilities
    assert responses[0]["result"]["serverInfo"] == {
        "name": "plangd",
        "version": version,
    }, responses
    assert responses[1]["result"] is None, responses

    process, responses = exchange(executable, [{"method": "exit"}])
    assert process.returncode == 1, process
    assert responses == [], responses

    check_document_sync(executable, initialize, shutdown)
    check_symbol_navigation(executable, initialize, shutdown)
