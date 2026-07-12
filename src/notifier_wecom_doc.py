from __future__ import annotations

import json
import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, Optional

from .utils import ROOT_DIR, env_first, run_command


class WeComDocNotifier:
    def __init__(self, logger):
        self.logger = logger
        self.node_path = env_first("PAPER_MONITOR_NODE_PATH", default="node") or "node"
        self.bridge_path = Path(
            env_first(
                "PAPER_MONITOR_WECOM_MCP_BRIDGE",
                default=str(ROOT_DIR / "scripts" / "wecom_mcp_bridge.mjs"),
            )
            or str(ROOT_DIR / "scripts" / "wecom_mcp_bridge.mjs")
        )
        self.verify_write = self._env_flag("PAPER_MONITOR_WECOM_DOC_VERIFY_WRITE", default=True)
        self._doc_tools: Optional[Dict[str, Dict[str, object]]] = None
        self._edit_doc_schema: Optional[Dict[str, object]] = None

    def _env_flag(self, name: str, default: bool) -> bool:
        raw = env_first(name)
        if raw is None:
            return default
        return str(raw).strip().lower() not in {"0", "false", "no", "off"}

    def _run_bridge(
        self,
        action: str,
        category: str,
        method: Optional[str] = None,
        args: Optional[Dict[str, object]] = None,
        timeout: int = 180,
    ) -> Dict[str, object]:
        command = [self.node_path, str(self.bridge_path), action, category]
        if method:
            command.append(method)

        temp_path = None
        try:
            if args is not None:
                with NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
                    json.dump(args, handle, ensure_ascii=False)
                    temp_path = handle.name
                command.extend(["--args-file", temp_path])

            completed = run_command(command, logger=self.logger, cwd=ROOT_DIR, timeout=timeout)
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "wecom doc bridge failed")
            output = completed.stdout.strip()
            return json.loads(output) if output else {}
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    def _load_doc_tools(self) -> Dict[str, Dict[str, object]]:
        if self._doc_tools is not None:
            return self._doc_tools
        listing = self._run_bridge("list", "doc", timeout=120)
        tools = listing.get("tools")
        if not isinstance(tools, list):
            raise RuntimeError("Unexpected tools/list response for doc category")
        resolved: Dict[str, Dict[str, object]] = {}
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name")
            if isinstance(name, str):
                resolved[name] = tool
        self._doc_tools = resolved
        return resolved

    def supports_tool(self, name: str) -> bool:
        return name in self._load_doc_tools()

    def get_edit_doc_schema(self) -> Dict[str, object]:
        if self._edit_doc_schema is not None:
            return self._edit_doc_schema
        tool = self._load_doc_tools().get("edit_doc_content")
        if not isinstance(tool, dict):
            raise RuntimeError("edit_doc_content not found in doc tools/list")
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict):
            raise RuntimeError("edit_doc_content missing inputSchema")
        self._edit_doc_schema = schema
        return schema

    def _unwrap_call_result(self, method: str, result: Dict[str, object]) -> Dict[str, Any]:
        content = result.get("content")
        text_items = []
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    text_items.append(item["text"])

        if result.get("isError") is True:
            detail = text_items[0] if text_items else json.dumps(result, ensure_ascii=False)
            raise RuntimeError(f"doc.{method} failed: {detail}")

        if not text_items:
            return result

        first = text_items[0]
        try:
            payload = json.loads(first)
        except json.JSONDecodeError:
            return {"text": first}

        if isinstance(payload, dict):
            errcode = payload.get("errcode")
            if isinstance(errcode, int) and errcode != 0:
                errmsg = payload.get("errmsg") or "unknown"
                raise RuntimeError(f"doc.{method} failed: errcode={errcode}, errmsg={errmsg}")
            return payload
        return {"value": payload}

    def _call_doc_tool(
        self,
        method: str,
        args: Dict[str, object],
        timeout: int = 180,
    ) -> Dict[str, Any]:
        result = self._run_bridge("call", "doc", method, args=args, timeout=timeout)
        return self._unwrap_call_result(method, result)

    def resolve_markdown_content_type(self) -> int:
        schema = self.get_edit_doc_schema()
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise RuntimeError("edit_doc_content schema missing properties")

        content_type_schema = properties.get("content_type")
        if not isinstance(content_type_schema, dict):
            raise RuntimeError("edit_doc_content schema missing content_type")
        if content_type_schema.get("type") != "integer":
            raise RuntimeError("edit_doc_content.content_type is not integer")
        enum_values = content_type_schema.get("enum")
        if not isinstance(enum_values, list) or 1 not in enum_values:
            raise RuntimeError("edit_doc_content.content_type does not accept markdown type 1")

        required_ok = False
        direct_required = schema.get("required")
        if isinstance(direct_required, list) and {"docid", "content", "content_type"}.issubset(set(direct_required)):
            required_ok = True
        for option in schema.get("oneOf", []):
            if not isinstance(option, dict):
                continue
            required = option.get("required")
            if isinstance(required, list) and {"docid", "content", "content_type"}.issubset(set(required)):
                required_ok = True
                break
        if not required_ok:
            raise RuntimeError("edit_doc_content schema does not allow docid/content/content_type input")
        return 1

    def _normalize_markdown(self, content: str) -> str:
        return content.replace("\r\n", "\n").rstrip()

    def _fetch_markdown(self, docid: str, timeout: int = 120) -> str:
        if not self.supports_tool("get_doc_content"):
            raise RuntimeError(
                "edit_doc_content returned ok, but live doc tools/list does not expose get_doc_content; "
                "cannot verify remote body, refusing silent success"
            )

        deadline = time.monotonic() + timeout
        task_id: Optional[str] = None
        while True:
            payload: Dict[str, object] = {"docid": docid, "type": 2}
            if task_id:
                payload["task_id"] = task_id
            remaining = max(10, int(deadline - time.monotonic()))
            result = self._call_doc_tool("get_doc_content", payload, timeout=min(60, remaining))

            if bool(result.get("task_done")):
                content = result.get("content")
                if not isinstance(content, str):
                    raise RuntimeError("doc.get_doc_content completed without markdown content")
                return content

            task_value = result.get("task_id")
            if not isinstance(task_value, str) or not task_value:
                raise RuntimeError("doc.get_doc_content did not return task_id for polling")
            task_id = task_value

            if time.monotonic() >= deadline:
                raise RuntimeError("Timed out waiting for doc.get_doc_content to finish")
            time.sleep(2)

    def verify_markdown(self, docid: str, expected_content: str) -> Dict[str, Any]:
        remote_content = self._fetch_markdown(docid)
        expected = self._normalize_markdown(expected_content)
        remote = self._normalize_markdown(remote_content)
        if remote != expected:
            expected_head = "\n".join(expected.splitlines()[:5])
            remote_head = "\n".join(remote.splitlines()[:5])
            raise RuntimeError(
                "Remote doc content mismatch after edit_doc_content: "
                f"expected_head={expected_head!r}, remote_head={remote_head!r}"
            )
        return {"verified": True}

    def edit_markdown(self, docid: str, content: str) -> Dict[str, object]:
        content_type = self.resolve_markdown_content_type()
        payload = {
            "docid": docid,
            "content": content,
            "content_type": content_type,
        }
        result = self._call_doc_tool("edit_doc_content", payload, timeout=300)
        if self.verify_write:
            result.update(self.verify_markdown(docid, content))
        return result
