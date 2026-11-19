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
