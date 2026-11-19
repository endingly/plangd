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


def check_definition(executable, initialize, shutdown):
    """Check lexical scopes, UTF-16 ranges and navigation after buffer edits."""
    uri = "file:///tmp/plangd-definition.ptx"
    comment = "/* 中文🚀 */"
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
        f"  {comment} .reg .u32 %r1;",
        f"  {comment} mov.u32 %r1, 2;",
        "  bra done;",
        "  mov.u32 %missing, 2;",
        "done:",
        "  ret;",
        "}",
    ]

    def position(line, symbol, shift=0):
        prefix = lines[line][:lines[line].index(symbol)]
        column = len(prefix.encode("utf-16-le")) // 2
        return {"line": line + shift, "character": column}

    def definition(request_id, cursor):
        return {
            "id": request_id,
            "method": "textDocument/definition",
            "params": {"textDocument": {"uri": uri}, "position": cursor},
        }

    def location(line, symbol, shift=0):
        start = position(line, symbol, shift)
        end = {**start, "character": start["character"] + len(symbol)}
        return {"uri": uri, "range": {"start": start, "end": end}}

    text = "\r\n".join(lines)
    process, responses = exchange(
        executable,
        [
            *initialize,
            definition(10, position(5, "%r1")),  # The buffer is not open yet.
            {
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": uri,
                        "languageId": "ptx",
                        "version": 1,
                        "text": text,
                    },
                },
            },
            definition(11, position(5, "%r1")),
            definition(12, position(12, "%r1")),
            definition(13, position(6, "done")),
            definition(14, position(13, "done")),
            definition(15, position(14, "%missing")),
            definition(16, position(5, ",")),  # The end of a reference is exclusive.
            {
                "method": "textDocument/didChange",
                "params": {
                    "textDocument": {"uri": uri, "version": 2},
                    "contentChanges": [
                        {"text": "// unsaved edit\r\n" + text + "\r\n.global .u32 ;"}
                    ],
                },
            },
            definition(17, position(12, "%r1", shift=1)),
            definition(18, position(13, "done", shift=1)),
            {
                "method": "textDocument/didClose",
                "params": {"textDocument": {"uri": uri}},
            },
            definition(19, position(13, "done", shift=1)),
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
    }
    for request_id, result in expected.items():
        assert results[request_id].get("result", ...) == result, results[request_id]


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
    check_definition(executable, initialize, shutdown)
