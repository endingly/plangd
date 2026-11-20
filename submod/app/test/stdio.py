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
            ".visible .entry demo() { .reg .u32 %r1;",
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


def check_semantic_diagnostics(executable, initialize, shutdown):
    """Check source ranges, recovery and replacement of semantic diagnostics."""
    uri = "file:///tmp/plangd-binding-diagnostics.ptx"
    valid = [
        ".version 8.0",
        ".target sm_90",
        ".address_size 64",
        ".extern .func helper();",
        ".entry first() {",
        "  .reg .u32 %r;",
        "  .reg .u32 %temp;",
        "  /* 中文🚀 */ mov.u32 %r, 0;",
        "  /* 中文🚀 */ bra done;",
        "  { .reg .u32 %r; mov.u32 %r, 0; }",
        "  call.uni helper;",
        "done:",
        "  ret;",
        "}",
        ".entry second() {",
        "  ret;",
        "}",
        "/* 中文🚀 */ .global .align 4 .u32 data;",
        ".global .u32 values[4];",
        ".func callee(.reg .u32 value);",
        ".func callee(.reg .u32 value) { ret; }",
    ]

    def edited(replacements):
        lines = valid.copy()
        for line, text in replacements.items():
            lines[line] = text
        return lines

    binding_errors = edited({
        6: "  .reg .u32 %r;",
        7: valid[7].replace("%r", "%unknown"),
        8: valid[8].replace("done", "missing"),
    })
    invalid_target = edited({8: valid[8].replace("done", "%r")})
    recovering_function = edited({
        6: "  { .reg .u32 ; }",
        7: valid[7].replace("0;", "%temp;"),
        15: "  bra outside_missing;",
    })
    bad_alignment = valid[17].replace("align 4", "align 3")
    overflow = "18446744073709551616"
    recovering_module = edited({3: ".extern .func ;", 17: bad_alignment})
    declaration_errors = edited({
        5: "  .reg .u320 %r;",
        6: "  .reg .v2 .pred %temp;",
        7: valid[7].replace("%r", "%unknown"),
        17: bad_alignment,
        18: valid[18].replace("[4]", f"[{overflow}]"),
        20: valid[20].replace(".u32", ".u64"),
    })
    partial_declarations = edited({
        5: declaration_errors[5],
        9: "  { .reg .u32 ; }",
        15: "  .reg .v2 .pred %p;",
        17: bad_alignment,
    })

    def token_range(lines, line, spelling):
        prefix = lines[line][:lines[line].index(spelling)]
        column = len(prefix.encode("utf-16-le")) // 2
        return {"start": {"line": line, "character": column},
                "end": {"line": line, "character": column + len(spelling)}}

    def change(version, lines):
        return {"method": "textDocument/didChange", "params": {
            "textDocument": {"uri": uri, "version": version},
            "contentChanges": [{"text": "\r\n".join(lines)}],
        }}

    init = [{
        **initialize[0], "params": {
            **initialize[0]["params"], "capabilities": {
                "textDocument": {"publishDiagnostics": {"relatedInformation": True}},
            },
        },
    }, *initialize[1:]]
    process, responses = exchange(executable, [
        *init,
        {"method": "textDocument/didOpen", "params": {"textDocument": {
            "uri": uri, "languageId": "ptx", "version": 1, "text": "\r\n".join(valid),
        }}},
        change(2, binding_errors),
        change(3, invalid_target),
        change(4, valid),
        change(5, recovering_function),
        {"id": 20, "method": "textDocument/definition", "params": {
            "textDocument": {"uri": uri},
            "position": token_range(recovering_function, 7, "%r")["start"],
        }},
        change(6, recovering_module),
        change(7, valid),
        change(8, declaration_errors),
        change(9, partial_declarations),
        change(10, valid),
        {"method": "textDocument/didClose", "params": {"textDocument": {"uri": uri}}},
        *shutdown,
    ])
    assert process.returncode == 0, process.stderr
    published = [response["params"] for response in responses
                 if response.get("method") == "textDocument/publishDiagnostics"]
    assert all(snapshot["uri"] == uri for snapshot in published), published
    assert [snapshot.get("version") for snapshot in published] == [*range(1, 11), None]
    (opened, errors, target, fixed, partial, module, restored, declarations,
     partial_decls, cleared, closed) = (
        snapshot["diagnostics"] for snapshot in published
    )
    assert opened == fixed == restored == cleared == closed == [], published
    assert len(errors) == 3, errors

    def diagnostic_at(diagnostics, lines, line, spelling, expected_message):
        expected_range = token_range(lines, line, spelling)
        match = next((item for item in diagnostics if item["range"] == expected_range), None)
        assert match is not None and expected_message in match["message"], diagnostics
        assert match["severity"] == 1 and match["source"] == "plangd", match
        return match

    duplicate = diagnostic_at(errors, binding_errors, 6, "%r", "Duplicate symbol")
    assert duplicate["relatedInformation"] == [{
        "location": {"uri": uri, "range": token_range(valid, 5, "%r")},
        "message": "Previous declaration is here.",
    }], duplicate
    diagnostic_at(errors, binding_errors, 7, "%unknown", "Unresolved")
    diagnostic_at(errors, binding_errors, 8, "missing", "Unresolved branch target")
    assert len(target) == 1, target
    diagnostic_at(target, invalid_target, 8, "%r", "must name a label")

    assert len(partial) == 2, partial
    assert any(item["range"]["start"]["line"] == 6 for item in partial), partial
    diagnostic_at(partial, recovering_function, 15, "outside_missing", "Unresolved branch target")
    assert all("%temp" not in item["message"] for item in partial), partial
    location = next(response["result"] for response in responses if response.get("id") == 20)
    assert location == {"uri": uri, "range": token_range(valid, 5, "%r")}, location
    assert module and all(item["range"]["start"]["line"] == 3 for item in module), module

    assert len(declarations) == 6, declarations
    diagnostic_at(declarations, declaration_errors, 5, ".u320", "Unknown register declaration type")
    diagnostic_at(declarations, declaration_errors, 6, ".v2", "Predicate register declarations")
    diagnostic_at(declarations, declaration_errors, 7, "%unknown", "Unresolved")
    diagnostic_at(declarations, declaration_errors, 17, "3", "power of two")
    diagnostic_at(declarations, declaration_errors, 18, overflow, "uint64_t")
    mismatch = diagnostic_at(declarations, declaration_errors, 20, "callee", "incompatible")
    assert mismatch["relatedInformation"][0]["location"] == {
        "uri": uri, "range": token_range(valid, 19, "callee"),
    }, mismatch

    assert len(partial_decls) == 3, partial_decls
    assert any(item["range"]["start"]["line"] == 9 for item in partial_decls), partial_decls
    diagnostic_at(partial_decls, partial_declarations, 15, ".v2", "Predicate register declarations")
    diagnostic_at(partial_decls, partial_declarations, 17, "3", "power of two")


def check_document_outline(executable, initialize, shutdown):
    """Check outline ranges and replacement using the current editor buffer."""
    uri = "file:///tmp/plangd-outline.ptx"
    prefix = "/* 中文🚀 */ "
    lines = [
        ".version 8.0",
        ".target sm_90",
        ".address_size 64",
        prefix + ".visible .entry sample_kernel() {",
        "  .reg .u32 %r;",
        "  ret;",
        "}",
        ".func helper() { ret; }",
        ".extern .func imported();",
        ".global .u32 first = 1, second[4];",
        ".const .u32 limits[2] = {1, 2};",
        ".shared .u32 omitted;",
    ]

    def span(source, line, spelling):
        index = source[line].index(spelling)
        column = len(source[line][:index].encode("utf-16-le")) // 2
        return {
            "start": {"line": line, "character": column},
            "end": {"line": line, "character": column + len(spelling)},
        }

    def symbol(source, line, name, kind, detail, declaration, end_line=None):
        bounds = span(source, line, declaration)
        if end_line is not None:
            bounds["end"] = span(source, end_line, "}")["end"]
        return {
            "name": name, "kind": kind, "detail": detail,
            "range": bounds, "selectionRange": span(source, line, name),
        }

    expected = [
        symbol(lines, 3, "sample_kernel", 12, ".entry", lines[3].removeprefix(prefix), 6),
        symbol(lines, 7, "helper", 12, ".func", lines[7]),
        symbol(lines, 8, "imported", 12, ".func", lines[8]),
        symbol(lines, 9, "first", 13, ".global", "first = 1"),
        symbol(lines, 9, "second", 13, ".global", "second[4]"),
        symbol(lines, 10, "limits", 14, ".const", lines[10]),
    ]
    edited = lines.copy()
    edited[3] = edited[3].replace("sample_kernel", "renamed_kernel")
    edited[7] = "// helper removed from the editor buffer"
    updated = [
        symbol(edited, 3, "renamed_kernel", 12, ".entry", edited[3].removeprefix(prefix), 6),
        *expected[2:],
    ]
    recovering = edited.copy()
    recovering[4] = "  .reg .u32 ;"

    init = [{
        **initialize[0], "params": {
            **initialize[0]["params"], "capabilities": {
                "textDocument": {"documentSymbol": {
                    "hierarchicalDocumentSymbolSupport": True,
                }},
            },
        },
    }, *initialize[1:]]
    opened = {"method": "textDocument/didOpen", "params": {"textDocument": {
        "uri": uri, "languageId": "ptx", "version": 1, "text": "\r\n".join(lines),
    }}}

    def change(version, source):
        return {"method": "textDocument/didChange", "params": {
            "textDocument": {"uri": uri, "version": version},
            "contentChanges": [{"text": "\r\n".join(source)}],
        }}

    def request(request_id):
        return {"id": request_id, "method": "textDocument/documentSymbol",
                "params": {"textDocument": {"uri": uri}}}

    process, responses = exchange(executable, [
        *init, request(9), opened, request(10),
        change(2, edited), request(11),
        change(3, recovering), request(12),
        change(4, ["// empty outline"]), request(13),
        {"method": "textDocument/didClose", "params": {"textDocument": {"uri": uri}}},
        request(14), *shutdown,
    ])
    assert process.returncode == 0, process.stderr
    results = {response["id"]: response for response in responses if "id" in response}
    for request_id, outline in {
        9: None, 10: expected, 11: updated, 12: updated, 13: [], 14: None,
    }.items():
        assert results[request_id].get("result", ...) == outline, results[request_id]
    recovered = next(
        response["params"] for response in responses
        if response.get("method") == "textDocument/publishDiagnostics"
        and response["params"].get("version") == 3
    )
    assert recovered["diagnostics"], recovered

    process, responses = exchange(executable, [*initialize, opened, request(10), *shutdown])
    assert process.returncode == 0, process.stderr
    result = next(response for response in responses if response.get("id") == 10)
    assert result.get("result", ...) == [
        {"name": item["name"], "kind": item["kind"],
         "location": {"uri": uri, "range": item["selectionRange"]}}
        for item in expected
    ], result


def check_editor_features(executable, initialize, shutdown):
    """Exercise navigation, folding, semantic tokens and call help on one PTX buffer."""
    uri = "file:///tmp/plangd-editor-features.ptx"
    lines = [
        ".version 8.0",
        ".target sm_90",
        ".address_size 64",
        ".global .u32 count;",
        ".const .u32 bias = 7;",
        ".func helper(.reg .u32 arg);",
        ".extern .func remote();",
        ".func (.reg .u32 result) combine(.reg .u32 lhs, .reg .u32 rhs) {",
        "  add.u32 result, lhs, rhs;",
        "  ret;",
        "}",
        ".func helper(.reg .u32 value) {",
        "  ret;",
        "}",
        "/* 中文🚀 */ .entry kernel() {",
        "  .reg .u32 %r<3>;",
        "  .reg .pred %p;",
        "  .reg .u64 %ptr;",
        "  /* 中文🚀 */ ld.global.u32 %r0, [count];",
        "  ld.const.u32 %r1, [bias];",
        "  st.global.u32 [count], %r0;",
        "  mov.u32 %r2, %tid.x;",
        "  @%p bra done;",
        "  {",
        "    .reg .u32 %r0;",
        "    mov.u32 %r0, 2;",
        "  }",
        "  call.uni helper, (%r0);",
        "  call remote;",
        "  call %ptr;",
        "  /* 中文🚀 */ call (%r2), combine, (%r0, %r1);",
        "done:",
        "  ret;",
        "}",
        ".alias alias_helper, helper;",
        ".entry aliased() { call alias_helper, (1); ret; }",
    ]

    def span(line, spelling):
        index = lines[line].index(spelling)
        column = len(lines[line][:index].encode("utf-16-le")) // 2
        return {
            "start": {"line": line, "character": column},
            "end": {"line": line, "character": column + len(spelling)},
        }

    def location(line, spelling):
        return {"uri": uri, "range": span(line, spelling)}

    def request(request_id, method, line=None, spelling=None, **params):
        if line is not None:
            params["position"] = span(line, spelling)["start"]
        return {"id": request_id, "method": f"textDocument/{method}", "params": {
            "textDocument": {"uri": uri}, **params,
        }}

    opened = {"method": "textDocument/didOpen", "params": {"textDocument": {
        "uri": uri, "languageId": "ptx", "version": 1, "text": "\r\n".join(lines),
    }}}
    partial = lines[30][:lines[30].index(" %r1")]
    edited = {"method": "textDocument/didChange", "params": {
        "textDocument": {"uri": uri, "version": 2},
        "contentChanges": [{"text": "\r\n".join([*lines[:30], partial])}],
    }}
    partial_signature = request(36, "signatureHelp")
    partial_signature["params"]["position"] = {
        "line": 30, "character": len(partial.encode("utf-16-le")) // 2,
    }
    process, responses = exchange(executable, [
        *initialize, opened,
        request(10, "definition", 27, "helper"),
        request(11, "definition", 28, "remote"),
        request(12, "definition", 18, "count"),
        request(13, "definition", 19, "bias"),
        request(14, "references", 20, "count", context={"includeDeclaration": True}),
        request(15, "references", 4, "bias", context={"includeDeclaration": False}),
        request(16, "prepareRename", 18, "count"),
        request(17, "documentHighlight", 18, "%r0"),
        request(18, "documentHighlight", 25, "%r0"),
        request(19, "documentHighlight", 22, "done"),
        request(20, "foldingRange"),
        request(21, "semanticTokens/full"),
        request(30, "signatureHelp", 30, "%r2"),
        request(31, "signatureHelp", 30, "%r0"),
        request(32, "signatureHelp", 30, "%r1"),
        request(33, "signatureHelp", 29, "%ptr"),
        request(34, "signatureHelp", 27, "%r0"),
        request(35, "signatureHelp", 28, "remote"),
        request(39, "definition", 35, "alias_helper"),
        request(40, "signatureHelp", 35, "1"),
        edited, partial_signature, request(37, "foldingRange"),
        {"method": "textDocument/didClose", "params": {"textDocument": {"uri": uri}}},
        request(38, "semanticTokens/full"), *shutdown,
    ])
    assert process.returncode == 0, process.stderr
    results = {item["id"]: item for item in responses if "id" in item}

    def highlights(*sites):
        return [{"range": span(line, word)} for line, word in sites]

    for request_id, expected in {
        10: location(11, "helper"),
        11: location(6, "remote"),
        12: location(3, "count"),
        13: location(4, "bias"),
        14: [location(3, "count"), location(18, "count"), location(20, "count")],
        15: [location(19, "bias")],
        16: None,
        17: highlights((15, "%r"), (18, "%r0"), (20, "%r0"), (27, "%r0"), (30, "%r0")),
        18: highlights((24, "%r0"), (25, "%r0")),
        19: highlights((22, "done"), (31, "done")),
        33: None,
        38: None,
        39: location(11, "helper"),
    }.items():
        assert results[request_id].get("result", ...) == expected, results[request_id]

    def fold(start, end):
        return {
            "startLine": start, "endLine": end,
            "startCharacter": span(start, "{")["end"]["character"],
            "endCharacter": span(end, "}")["start"]["character"],
        }

    assert results[20]["result"] == [fold(7, 10), fold(11, 13), fold(14, 33), fold(23, 26)]
    assert results[37]["result"] == [fold(7, 10), fold(11, 13), fold(23, 26)]

    legend = results[1]["result"]["capabilities"]["semanticTokensProvider"]["legend"]
    data = results[21]["result"]["data"]
    tokens = {}
    line = column = previous_end = 0
    for delta_line, delta_start, length, kind, modifiers in zip(*[iter(data)] * 5, strict=True):
        line += delta_line
        column = delta_start if delta_line else column + delta_start
        assert length > 0 and (delta_line or column >= previous_end) and modifiers == 0
        tokens[line, column, length] = legend["tokenTypes"][kind]
        previous_end = column + length
    for line, spelling, kind in [
        (3, "count", "variable"), (4, "bias", "variable"),
        (5, "helper", "function"), (11, "helper", "function"),
        (14, "kernel", "function"), (15, "%r", "register"),
        (18, "ld", "keyword"), (18, ".global", "modifier"), (18, ".u32", "modifier"),
        (18, "%r0", "register"), (18, "count", "variable"),
        (19, "bias", "variable"), (21, "%tid", "register"),
        (22, "done", "label"), (27, "helper", "function"), (31, "done", "label"),
    ]:
        column = span(line, spelling)["start"]["character"]
        assert tokens.get((line, column, len(spelling))) == kind, (line, spelling, tokens)

    combine = (".func (.reg .u32 result) combine(.reg .u32 lhs, .reg .u32 rhs)",
               [".reg .u32 result", ".reg .u32 lhs", ".reg .u32 rhs"])
    for request_id, (signature, parameters), active in [
        (30, combine, 0), (31, combine, 1), (32, combine, 2),
        (34, (".func helper(.reg .u32 value)", [".reg .u32 value"]), 0),
        (35, (".func remote()", []), None),
        (36, combine, 2),
        (40, (".func helper(.reg .u32 value)", [".reg .u32 value"]), 0),
    ]:
        help_result = results[request_id].get("result")
        assert help_result, results[request_id]
        info = help_result["signatures"][0]
        assert info["label"] == signature, info
        assert [parameter["label"] for parameter in info["parameters"]] == parameters, info
        assert help_result["activeSignature"] == 0 and help_result.get("activeParameter") == active

    folding_init = [{
        **initialize[0], "params": {
            **initialize[0]["params"], "capabilities": {
                "textDocument": {"foldingRange": {"lineFoldingOnly": True}},
            },
        },
    }, *initialize[1:]]
    process, responses = exchange(executable, [
        *folding_init, opened, request(20, "foldingRange"), *shutdown,
    ])
    assert process.returncode == 0, process.stderr
    line_folds = next(item for item in responses if item.get("id") == 20)
    assert line_folds["result"] == [
        {"startLine": 7, "endLine": 9}, {"startLine": 11, "endLine": 12},
        {"startLine": 14, "endLine": 32}, {"startLine": 23, "endLine": 25},
    ]


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


def check_instruction_completion(executable, initialize, shutdown):
    """Check completion and source edits on successive unfinished snapshots."""
    uri = "file:///tmp/plangd-completion.ptx"
    header = ".version 8.0\r\n.target sm_90\r\n.address_size 64\r\n"
    kernel = ".visible .entry example() {\r\n"
    body = header + kernel

    def configured(version, target):
        return f".version {version}\r\n.target {target}\r\n.address_size 64\r\n" + kernel

    modern = configured("9.3", "sm_100a")
    region = ".version 8.2\r\n.target sm_80\r\n.entry first() { ret; }\r\n"
    cases = [
        (body + "  mo|", "mov", "mo"),
        (body + "  /* 中文🚀 */ loop: @!%p1 mo|xxx.u32 %r1, 0;", "mov", "moxxx"),
        (body + "  { ret; } ad|", "add", "ad"),
        (body + "  .loc 1 2 3\r\n  re|", "ret", "re"),
        (body + "  ret; |}", "add", ""),
        (body + "  ret; re|}", "ret", "re"),
        (header + '.entry example() .pragma "nounroll"; {mo|}', "mov", "mo"),
        (body + "  ret; no_such_opcode|", None, "no_such_opcode"),
        (body + "  mov.u32 %r1,\r\n  mo|", None, None),
        (body + "  mov.u32 %r1, mo|;", None, None),
        (body + "  mov.u32 {%r1, mo|}, %r2;", None, None),
        (body + "  // ret; mo|", None, None),
        (body + "  /* 中文🚀\r\n  mo| */", None, None),
        (body + "  /* unterminated mo|", None, None),
        (body + '  .pragma "ret; mo|";', None, None),
        (body + "  @%p1|", None, None),
        (body + "  @!%p|1 mov.u32 %r1, 0;", None, None),
        (body + "  mo|ve_here: ret;", None, None),
        (body + "  mov.u|32 %r1, 0;", ".u32", ".u32"),
        (body + "}\r\nmo|", None, None),
        (header + ".global .u32 arr[2] = {mo|};", None, None),
        (header + ".entry mo|() {}", None, None),
        (body + "  ld.glo|", ".global", ".glo"),
        (body + "  /* 中文🚀 */ loop: @!%p1 ld.glo|xxx.u32 %r1, [%rd1];", ".global", ".gloxxx"),
        (body + "  ld.global.|", ".u32", "."),
        (body + "  ld.global.v2.u| %r1, [%rd1];", ".u32", ".u"),
        (body + "  ld .global\r\n  .u|", ".u32", ".u"),
        (modern + "  ld.param::|", ".param::entry", ".param::"),
        (modern + "  ld.param::en|try.u32", ".param::entry", ".param::entry"),
        (body + "  min.xorsign.a|.f32", ".xorsign.abs", ".xorsign.a"),
        (body + "  min.xor|sign.a.f32", ".xorsign.abs", ".xorsign.a"),
        (body + "  min.xor|sign.abs.f32", ".xorsign.abs", ".xorsign.abs"),
        (body + "  fence.cta.acq_|", ".acq_rel", ".acq_"),
        (body + "  fence.acq_rel.c|", ".cta", ".c"),
        (modern + "  add.f32.f16.sa|", ".sat", ".sa"),
        (body + "  ld.global.shared.u|", None, ".u"),
        (body + "  ld.global.unsup|", None, ".unsup"),
        (body + "  unknown_opcode.glo|", None, ".glo"),
        (body + "  ld.global.u32 %r1, .glo|", None, None),
        (body + "  ld /* .glo| */ .global.u32", None, None),
        (body + "  .reg .u|32 %r1;", None, None),
        (header + ".target sm_90, .glo|", None, None),
        (configured("8.1", "sm_80") + "  ld.mm|", None, ".mm"),
        (configured("8.2", "sm_61") + "  ld.mm|", None, ".mm"),
        (configured("8.2", "sm_80") + "  ld.mm|", ".mmio", ".mm"),
        (configured("8.1", "sm_80") + "  ld.mmio.rel|", None, ".rel"),
        (configured("8.2", "sm_80") + "  ld.mmio.rel|", ".relaxed", ".rel"),
        (configured("8.0", "sm_75") + "  min.Na|", None, ".Na"),
        (configured("8.0", "sm_80") + "  min.Na|", ".NaN", ".Na"),
        (configured("8.0", "sm_80") + "  ld.shared::cl|", None, ".shared::cl"),
        (body + "  ld.shared::cl|", ".shared::cluster", ".shared::cl"),
        (body + "  setmaxnr|", None, "setmaxnr"),
        (configured("8.0", "sm_90a") + "  setmaxnr|", "setmaxnreg", "setmaxnr"),
        (body + "  setmaxnreg.i|", None, ".i"),
        (configured("8.0", "sm_90a") + "  setmaxnreg.i|", ".inc", ".i"),
        (configured("8.8", "sm_100f") + "  setmaxnreg.i|", ".inc", ".i"),
        (configured("8.8", "sm_100") + "  setmaxnreg.i|", None, ".i"),
        (region + ".target sm_61\r\n" + kernel + "  ld.mm|", None, ".mm"),
        (region.replace("ret;", "ld.mm|") + ".target sm_61\r\n", ".mmio", ".mm"),
        (region.replace("8.2", "8.1") + ".target\r\n" + kernel + "  ld.mm|", ".mmio", ".mm"),
        (region.replace("8.2", "8.1") + ".target sm_999\r\n" + kernel + "  ld.mm|", ".mmio", ".mm"),
        (".version 8.1\r\n" + kernel + "  ld.mm|", ".mmio", ".mm"),
        (".target sm_61\r\n" + kernel + "  ld.mm|", ".mmio", ".mm"),
    ]
    excluded = {body + "  ld.global.|": {".global", ".shared"}}

    def column(text):
        return len(text.encode("utf-16-le")) // 2

    messages = [
        *initialize,
        {
            "method": "textDocument/didOpen",
            "params": {"textDocument": {
                "uri": uri, "languageId": "ptx", "version": 0, "text": header,
            }},
        },
    ]
    expected = {}
    for index, (marked, label, spelling) in enumerate(cases, start=1):
        left, right = marked.split("|")
        line = left.count("\n")
        line_prefix = left.rsplit("\n", 1)[-1]
        cursor = column(line_prefix)
        request_id = index + 100
        messages.extend([
            {
                "method": "textDocument/didChange",
                "params": {
                    "textDocument": {"uri": uri, "version": index},
                    "contentChanges": [{"text": left + right}],
                },
            },
            {
                "method": "textDocument/completion",
                "id": request_id,
                "params": {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": cursor},
                    **({"context": {"triggerKind": 2, "triggerCharacter": "."}}
                       if left.endswith(".") else {}),
                },
            },
        ])
        if spelling is None:
            expected[request_id] = (label, None, "", set())
            continue
        line_text = line_prefix + right.split("\r\n", 1)[0]
        start = len(line_prefix)
        if spelling:
            start = line_text.rfind(spelling, max(0, start - len(spelling)), start + len(spelling))
            assert start >= 0, marked
        begin = column(line_prefix[:start])
        edit_range = {
            "start": {"line": line, "character": begin},
            "end": {"line": line, "character": begin + column(spelling)},
        }
        expected[request_id] = (label, edit_range, line_prefix[start:], excluded.get(marked, set()))

    process, responses = exchange(executable, [*messages, *shutdown])
    assert process.returncode == 0, process.stderr
    results = {response["id"]: response for response in responses if "id" in response}
    assert any(
        event.get("params", {}).get("version") == 1 and event["params"]["diagnostics"]
        for event in responses if event.get("method") == "textDocument/publishDiagnostics"
    ), responses
    for request_id, (label, edit_range, prefix, forbidden) in expected.items():
        result = results[request_id]["result"]
        if label is None:
            assert result == (None if edit_range is None else []), (request_id, result)
            continue
        labels = [item["label"] for item in result]
        assert label in labels and labels == sorted(set(labels)), (request_id, labels)
        assert forbidden.isdisjoint(labels), (request_id, labels)
        for item in result:
            assert item["label"].startswith(prefix) and item["kind"] == 14, item
            assert item["textEdit"] == {"range": edit_range, "newText": item["label"]}, item
        if label == "mov":
            move = next(item for item in result if item["label"] == label)
            assert "Move a scalar register" in move["documentation"], move


def check_symbol_references(executable, initialize, shutdown):
    """Check declaration and reference queries against bound symbol identities."""
    uri = "file:///tmp/plangd-references.ptx"
    lines = [
        ".version 8.0",
        ".target sm_90",
        ".address_size 64",
        ".entry first() {",
        "  .reg .pred %guard;",
        "  .reg .u32 %r, %unused;",
        "  .reg .u32 %tmp<4>;",
        "  /* 中文🚀 done */ @%guard add.u32 %r, %tmp1, %tmp2;",
        "  {",
        "    .reg .u32 %r;",
        "    add.u32 %r, %r, %tmp1;",
        "  }",
        "  bra done;",
        "done:",
        "  mov.u32 %r, %missing;",
        "  ret;",
        "}",
        ".entry second() {",
        "  .reg .u32 %r;",
        "  mov.u32 %r, 0;",
        "  bra done;",
        "done:",
        "  ret;",
        "}",
    ]

    def location(line, spelling, occurrence=0, shift=0):
        text = lines[line]
        offset = text.index(spelling)
        for _ in range(occurrence):
            offset = text.index(spelling, offset + len(spelling))
        column = len(text[:offset].encode("utf-16-le")) // 2
        return {"uri": uri, "range": {
            "start": {"line": line + shift, "character": column},
            "end": {"line": line + shift, "character": column + len(spelling)},
        }}

    def reference(request_id, where, include):
        return {"id": request_id, "method": "textDocument/references", "params": {
            "textDocument": {"uri": uri}, "position": where["range"]["start"],
            "context": {"includeDeclaration": include},
        }}

    register = location(5, "%r")
    use = location(7, "%r")
    last_use = location(14, "%r")
    unused = location(5, "%unused")
    inner = [location(10, "%r"), location(10, "%r", occurrence=1)]
    group = location(6, "%tmp")
    member_1 = [location(7, "%tmp1"), location(10, "%tmp1")]
    member_2 = location(7, "%tmp2")
    cases = [
        (register, False, [use, last_use]),
        (use, True, [register, use, last_use]),
        (inner[1], True, [location(9, "%r"), *inner]),
        (location(18, "%r"), False, [location(19, "%r")]),
        (location(7, "%guard"), True, [location(4, "%guard"), location(7, "%guard")]),
        (group, True, [group, member_1[0], member_2, member_1[1]]),
        (member_1[1], True, [group, *member_1]),
        (member_2, False, [member_2]),
        (location(12, "done"), True, [location(12, "done"), location(13, "done")]),
        (location(21, "done"), False, [location(20, "done")]),
        (unused, False, []),
        (unused, True, [unused]),
        (location(14, "%missing"), True, None),
        (location(7, "done"), True, None),
        (location(13, ":"), True, None),
    ]
    text = "\r\n".join(lines)
    request_ids = range(100, 100 + len(cases))
    shifted_register = location(5, "%r", shift=1)
    process, responses = exchange(executable, [
        *initialize,
        reference(10, register, True),
        {"method": "textDocument/didOpen", "params": {"textDocument": {
            "uri": uri, "languageId": "ptx", "version": 1, "text": text,
        }}},
        *(reference(request_id, where, include)
          for request_id, (where, include, _) in zip(request_ids, cases)),
        {"method": "textDocument/didChange", "params": {
            "textDocument": {"uri": uri, "version": 2},
            "contentChanges": [{"text": "// unsaved edit\r\n"
                                + text.replace("mov.u32 %r, %missing;", "mov.u32 %r, ;")}],
        }},
        reference(11, shifted_register, True),
        {"method": "textDocument/didClose", "params": {"textDocument": {"uri": uri}}},
        reference(12, shifted_register, True),
        *shutdown,
    ])
    assert process.returncode == 0, process.stderr
    results = {response["id"]: response for response in responses if "id" in response}
    assert results[10]["result"] is None and results[12]["result"] is None, results
    assert results[11]["result"] == [shifted_register, location(7, "%r", shift=1)], results[11]
    for request_id, (_, _, expected) in zip(request_ids, cases):
        assert results[request_id]["result"] == expected, (request_id, results[request_id])


def check_symbol_completion(executable, initialize, shutdown):
    """Check scope, recovery and edit ranges for symbolic instruction operands."""
    uri = "file:///tmp/plangd-symbol-completion.ptx"
    header = ".version 8.0\r\n.target sm_90\r\n.address_size 64\r\n"
    before = header + ".entry foreign() { .reg .u32 %r_foreign; loop_foreign: ret; }\r\n"
    kernel = before + ".entry example() {\r\n"
    registers = kernel + "  .reg .u32 %r<4>;\r\n"
    group = {f"%r{index}": ".reg .u32 %r<4>;" for index in range(4)}
    large = kernel + "  .reg .u32 %big<1000000000>;\r\n"
    cases = [
        (registers + "  mov.u32 %|", "%", group),
        (registers + "  /* 中文🚀 */ mov.u32 %r|junk, 0;", "%rjunk", group),
        (registers + "  mov.u32 %r0, |", "", group),
        (registers + "  ld.global.u32 %r0, [%r|+4];", "%r", group),
        (registers + "  { .reg .s32 %r1; mov.s32 %r|", "%r",
         {**group, "%r1": ".reg .s32 %r1;"}),
        (registers + "  { .reg .s32 %r_nested; ret; } mov.u32 %r|", "%r", group),
        (registers + "  .reg .u32 plain; mov.u32 pl|", "pl", {"plain": ".reg .u32 plain;"}),
        (registers + "  mov.u32 %missing|", "%missing", None),
        (registers + "  // mov.u32 %r|", "%r", None),
        (registers + "  .reg .u32 %r|", "%r", None),
        (registers + "  @%r|", "%r", None),
        (registers + "  @!%p mov.u32 %r|1, 0;", "%r1", group),
        (kernel + "  bra.uni loo|\r\n  loop_local: ret;\r\n}", "loo",
         {"loop_local": "loop_local:"}),
        (kernel + "  { loop_nested: ret; }\r\n  bra |", "", {"loop_nested": "loop_nested:"}),
        (kernel + "  loop_local: bra loop_local, loo|", "loo", None),
        (kernel + "  bra loo|", "loo", None),
        (large + "  mov.u32 %big|", "%big",
         {f"%big{index}": ".reg .u32 %big<1000000000>;" for index in range(256)}),
        (large + "  mov.u32 %big999999999|", "%big999999999",
         {"%big999999999": ".reg .u32 %big<1000000000>;"}),
    ]

    def utf16(text):
        return len(text.encode("utf-16-le")) // 2

    messages = [*initialize, {"method": "textDocument/didOpen", "params": {
        "textDocument": {"uri": uri, "languageId": "ptx", "version": 0, "text": header},
    }}]
    expected = {}
    for request_id, (marked, fragment, declarations) in enumerate(cases, start=100):
        left, right = marked.split("|")
        prefix = left.rsplit("\r\n", 1)[-1]
        line = left.count("\r\n")
        current_line = prefix + right.split("\r\n", 1)[0]
        start = current_line.rfind(fragment, max(0, len(prefix) - len(fragment)),
                                   len(prefix) + len(fragment))
        assert start >= 0, marked
        edit_range = {
            "start": {"line": line, "character": utf16(prefix[:start])},
            "end": {"line": line, "character": utf16(prefix[:start] + fragment)},
        }
        expected[request_id] = (edit_range, declarations, fragment == "%big")
        messages.extend([
            {"method": "textDocument/didChange", "params": {
                "textDocument": {"uri": uri, "version": request_id},
                "contentChanges": [{"text": left + right}],
            }},
            {"method": "textDocument/completion", "id": request_id, "params": {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": utf16(prefix)},
                **({"context": {"triggerKind": 2, "triggerCharacter": "%"}}
                   if fragment == "%" else {}),
            }},
        ])

    process, responses = exchange(executable, [*messages, *shutdown])
    assert process.returncode == 0, process.stderr
    results = {response["id"]: response for response in responses if "id" in response}
    for request_id, (edit_range, declarations, incomplete) in expected.items():
        result = results[request_id]["result"]
        if declarations is None:
            assert result is None, (request_id, result)
            continue
        assert result is not None, cases[request_id - 100][0]
        if incomplete:
            assert result["isIncomplete"] is True, (request_id, result)
            result = result["items"]
        assert [item["label"] for item in result] == sorted(declarations), (request_id, result)
        for item in result:
            label = item["label"]
            detail = declarations[label]
            assert item["kind"] == (18 if detail.endswith(":") else 6), item
            assert item["detail"] == detail, item
            assert item["textEdit"] == {"range": edit_range, "newText": label}, item


def check_symbol_rename(executable, initialize, shutdown):
    """Check exact single-document edits and rejection of binding changes."""
    uri = "file:///tmp/plangd-rename.ptx"
    lines = [
        ".version 8.0",
        ".target sm_90",
        ".address_size 64",
        ".entry first() {",
        "  .reg .u32 %outer, %unused;",
        "  .reg .pred %guard;",
        "  .reg .u32 %temp<4>;",
        "  /* 中文🚀 %outer done */ @%guard add.u32 %outer, %outer, %temp1;",
        "  {",
        "    .reg .u32 %inner;",
        "    mov.u32 %outer, %inner;",
        "  }",
        "  { .reg .u32 %outer; mov.u32 %outer, 0; }",
        "  bra done;",
        "other:",
        "  bra done;",
        "done:",
        "  mov.u32 %unused, %unbound;",
        "  ret;",
        "}",
        ".entry second() {",
        "  .reg .u32 %outer;",
        "  mov.u32 %outer, 0;",
        "  bra done;",
        "done:",
        "  ret;",
        "}",
        ".entry groups() {",
        "  .reg .u32 %clash5, %bundle1<2>, %tmp<12>, %other<12>;",
        "  /* 中文🚀 %tmp0 */ add.u32 %tmp0, %tmp11, %unbound0;",
        "  {",
        "    .reg .u32 %capture11;",
        "    mov.u32 %tmp11, %capture11;",
        "  }",
        "  { .reg .u32 %tmp<12>; mov.u32 %tmp0, %tmp11; }",
        "  ret;",
        "}",
        ".entry more_groups() {",
        "  .reg .u32 %huge<1000000000>;",
        "  mov.u32 %huge999999999, 0;",
        "  ret;",
        "}",
    ]

    def span(line, spelling, occurrence=0, shift=0):
        text = lines[line]
        offset = text.index(spelling)
        for _ in range(occurrence):
            offset = text.index(spelling, offset + len(spelling))
        column = len(text[:offset].encode("utf-16-le")) // 2
        return {"start": {"line": line + shift, "character": column},
                "end": {"line": line + shift, "character": column + len(spelling)}}

    def request(request_id, at, new_name=None):
        params = {"textDocument": {"uri": uri}, "position": at["start"]}
        method = "prepareRename"
        if new_name is not None:
            method = "rename"
            params["newName"] = new_name
        return {"id": request_id, "method": f"textDocument/{method}", "params": params}

    def edits(ranges, spelling):
        return [{"range": edit_range, "newText": spelling} for edit_range in ranges]

    def versioned(ranges, spelling, version=1):
        return {"documentChanges": [{"textDocument": {"uri": uri, "version": version},
                                     "edits": edits(ranges, spelling)}]}

    outer = span(4, "%outer")
    uses = [span(7, "%outer", 1), span(7, "%outer", 2), span(10, "%outer")]
    inner = span(9, "%inner")
    label = span(16, "done")
    branch = span(13, "done")
    group = span(6, "%temp")
    member = span(7, "%temp1")
    group_decl = span(28, "%tmp")
    group_uses = [span(29, "%tmp", 1), span(29, "%tmp", 2), span(32, "%tmp")]
    shifted = span(4, "%outer", shift=1)
    text = "\r\n".join(lines)
    open_document = {"method": "textDocument/didOpen", "params": {"textDocument": {
        "uri": uri, "languageId": "ptx", "version": 1, "text": text,
    }}}
    init = [{
        **initialize[0], "params": {
            **initialize[0]["params"], "capabilities": {
                "textDocument": {"rename": {"prepareSupport": True}},
                "workspace": {"workspaceEdit": {"documentChanges": True}},
            },
        },
    }, *initialize[1:]]
    process, responses = exchange(executable, [
        *init,
        request(10, outer),
        open_document,
        request(11, uses[1]),
        request(12, label),
        request(13, group),
        request(14, member),
        request(15, span(7, "%outer")),  # Comment spelling.
        request(16, outer, "%outer"),
        request(17, uses[0], "%new_outer"),
        request(18, branch, "finish"),
        request(19, span(12, "%outer", 1), "%b"),
        request(20, span(7, "%guard"), "%p"),
        request(21, outer, "%unused"),
        request(22, outer, "%inner"),
        request(23, inner, "%outer"),
        request(24, outer, "%unbound"),
        request(25, outer, "%temp1"),
        request(26, label, "other"),
        request(27, outer, "%tid"),
        request(28, outer, "%bad name"),
        request(29, member, "%new_temp"),
        request(30, outer, ""),
        request(31, outer),  # Rename computes an edit and leaves the snapshot intact.
        request(34, group, "%renamed"),
        request(35, group_decl),
        request(36, span(29, "%tmp0", 1)),
        request(37, group_decl, "%buffer"),
        request(38, group_decl, "%clash"),
        request(39, group_decl, "%bundle"),
        request(40, group_decl, "%other"),
        request(41, group_decl, "%capture"),
        request(42, group_decl, "%unbound"),
        request(43, span(38, "%huge"), "%h"),
        request(44, span(34, "%tmp"), "%local"),
        request(45, span(29, "%tmp0", 1), "%base"),
        {"method": "textDocument/didChange", "params": {
            "textDocument": {"uri": uri, "version": 2},
            "contentChanges": [{"text": "// unsaved\r\n" + text + "\r\n.global .u32 ;"}],
        }},
        request(32, shifted, "%updated"),
        {"method": "textDocument/didClose", "params": {"textDocument": {"uri": uri}}},
        request(33, shifted, "%updated"),
        *shutdown,
    ])
    assert process.returncode == 0, process.stderr
    results = {response["id"]: response for response in responses if "id" in response}
    assert results[1]["result"]["capabilities"]["renameProvider"] == {"prepareProvider": True}
    expected = {
        10: None, 14: None, 15: None, 16: None, 36: None,
        11: {"range": uses[1], "placeholder": "%outer"},
        12: {"range": label, "placeholder": "done"},
        13: {"range": group, "placeholder": "%temp"},
        17: versioned([outer, *uses], "%new_outer"),
        18: versioned([branch, span(15, "done"), label], "finish"),
        19: versioned([span(12, "%outer"), span(12, "%outer", 1)], "%b"),
        20: versioned([span(5, "%guard"), span(7, "%guard")], "%p"),
        31: {"range": outer, "placeholder": "%outer"},
        32: versioned([shifted, span(7, "%outer", 1, 1), span(7, "%outer", 2, 1),
                       span(10, "%outer", shift=1)], "%updated", version=2),
        34: versioned([group, span(7, "%temp")], "%renamed"),
        35: {"range": group_decl, "placeholder": "%tmp"},
        37: versioned([group_decl, *group_uses], "%buffer"),
        43: versioned([span(38, "%huge"), span(39, "%huge")], "%h"),
        44: versioned([span(34, "%tmp"), span(34, "%tmp", 1), span(34, "%tmp", 2)],
                      "%local"),
    }
    for request_id, result in expected.items():
        assert results[request_id].get("result", ...) == result, results[request_id]
    for request_id in [*range(21, 31), 33, *range(38, 43), 45]:
        error = results[request_id]["error"]
        assert error["code"] == -32803 and error["message"], results[request_id]

    process, responses = exchange(executable, [
        *initialize, open_document, request(17, outer, "%plain"),
        request(37, group_decl, "%buffer"), *shutdown,
    ])
    assert process.returncode == 0, process.stderr
    result = next(response for response in responses if response.get("id") == 17)
    assert result["result"] == {"changes": {uri: edits([outer, *uses], "%plain")}}, result
    group_result = next(response for response in responses if response.get("id") == 37)
    assert group_result["result"] == {
        "changes": {uri: edits([group_decl, *group_uses], "%buffer")},
    }, group_result


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
    assert capabilities["referencesProvider"] is True, capabilities
    assert capabilities["documentHighlightProvider"] is True, capabilities
    assert capabilities["documentSymbolProvider"] is True, capabilities
    assert capabilities["foldingRangeProvider"] is True, capabilities
    assert capabilities["semanticTokensProvider"]["full"] is True, capabilities
    assert capabilities["signatureHelpProvider"]["triggerCharacters"] == ["(", ","], capabilities
    assert capabilities["renameProvider"] is True, capabilities
    assert capabilities["hoverProvider"] is True, capabilities
    assert capabilities["completionProvider"]["triggerCharacters"] == [".", "%"], capabilities
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
    check_semantic_diagnostics(executable, initialize, shutdown)
    check_document_outline(executable, initialize, shutdown)
    check_editor_features(executable, initialize, shutdown)
    check_symbol_navigation(executable, initialize, shutdown)
    check_symbol_references(executable, initialize, shutdown)
    check_instruction_completion(executable, initialize, shutdown)
    check_symbol_completion(executable, initialize, shutdown)
    check_symbol_rename(executable, initialize, shutdown)
