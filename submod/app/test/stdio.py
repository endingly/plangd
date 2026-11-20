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
        10: None, 13: None, 14: None, 15: None, 16: None,
        11: {"range": uses[1], "placeholder": "%outer"},
        12: {"range": label, "placeholder": "done"},
        17: versioned([outer, *uses], "%new_outer"),
        18: versioned([branch, span(15, "done"), label], "finish"),
        19: versioned([span(12, "%outer"), span(12, "%outer", 1)], "%b"),
        20: versioned([span(5, "%guard"), span(7, "%guard")], "%p"),
        31: {"range": outer, "placeholder": "%outer"},
        32: versioned([shifted, span(7, "%outer", 1, 1), span(7, "%outer", 2, 1),
                       span(10, "%outer", shift=1)], "%updated", version=2),
    }
    for request_id, result in expected.items():
        assert results[request_id].get("result", ...) == result, results[request_id]
    for request_id in [*range(21, 31), 33]:
        error = results[request_id]["error"]
        assert error["code"] == -32803 and error["message"], results[request_id]

    process, responses = exchange(executable, [
        *initialize, open_document, request(17, outer, "%plain"), *shutdown,
    ])
    assert process.returncode == 0, process.stderr
    result = next(response for response in responses if response.get("id") == 17)
    assert result["result"] == {"changes": {uri: edits([outer, *uses], "%plain")}}, result


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
    check_symbol_navigation(executable, initialize, shutdown)
    check_symbol_references(executable, initialize, shutdown)
    check_instruction_completion(executable, initialize, shutdown)
    check_symbol_completion(executable, initialize, shutdown)
    check_symbol_rename(executable, initialize, shutdown)
