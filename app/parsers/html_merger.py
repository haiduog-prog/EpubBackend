"""Extract translatable HTML text while preserving the source DOM."""

from __future__ import annotations

import html as html_lib
import re
from typing import Any, Dict, List, Tuple

from bs4 import BeautifulSoup, NavigableString, Tag

from app.schemas.translation import HTMLInputItem, HTMLTranslationItem


SEMANTIC_BLOCK_TAGS = {
    "p", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
    "dt", "dd", "td", "th", "figcaption",
}
MEDIA_TAGS = {"img", "svg", "video", "audio", "iframe", "source", "track"}
VOID_TAGS = MEDIA_TAGS | {"br", "hr", "input", "embed", "param", "meta", "link", "wbr"}
IGNORED_TEXT_PARENTS = {"script", "style", "noscript"}
MARKER_RE = re.compile(r"⟦html:(node_[0-9]+):(open|close|void):([0-9]+)⟧")
ANY_MARKER_RE = re.compile(r"⟦html:[^⟧]*⟧")


class HTMLMarkerValidationError(ValueError):
    """A translation removed, duplicated, or reordered protected markup."""


class HTMLMerger:
    """Keep visible text compatibility while carrying protected HTML markers."""

    @staticmethod
    def protect_markers(value: str) -> Tuple[str, Dict[str, str]]:
        """Hide markup markers from text QA/correction without losing them.

        The private-use tokens contain no ASCII letters, so the foreign-word
        rule cannot mistake the marker command (for example ``close``) for a
        leaked untranslated word.  Restoration is strict: a corrector that
        removes, duplicates, or changes a token fails before HTML assembly.
        """
        replacements: Dict[str, str] = {}

        def replace(match: re.Match[str]) -> str:
            token = f"\ue000{len(replacements)}\ue001"
            replacements[token] = match.group(0)
            return token

        return ANY_MARKER_RE.sub(replace, value), replacements

    @staticmethod
    def restore_markers(value: str, replacements: Dict[str, str]) -> str:
        restored = value
        expected_markers = set(replacements.values())
        unexpected = [
            marker
            for marker in ANY_MARKER_RE.findall(restored)
            if marker not in expected_markers
        ]
        if unexpected:
            raise HTMLMarkerValidationError("Bản hiệu đính chứa marker HTML không hợp lệ.")
        for token, marker in replacements.items():
            if restored.count(token) != 1:
                raise HTMLMarkerValidationError("Bản hiệu đính đã làm mất hoặc lặp marker HTML.")
            restored = restored.replace(token, marker)
        return restored

    @staticmethod
    def _visible_text(value: Any) -> str:
        if isinstance(value, NavigableString):
            return str(value)
        if not isinstance(value, Tag):
            return ""
        parts: List[str] = []
        for child in value.children:
            if isinstance(child, Tag) and child.name == "br":
                parts.append(" ")
            elif isinstance(child, Tag) and child.name in IGNORED_TEXT_PARENTS:
                continue
            else:
                parts.append(HTMLMerger._visible_text(child))
        return "".join(parts)

    @staticmethod
    def _tag_opening(tag: Tag) -> str:
        clone = BeautifulSoup("", "html.parser").new_tag(tag.name, attrs=dict(tag.attrs))
        return str(clone).replace(f"</{tag.name}>", "")

    @staticmethod
    def _serialize_children(tag: Tag, item_id: str, marker_map: Dict[str, str]) -> str:
        counter = [0]

        def visit(node: Any) -> str:
            if isinstance(node, NavigableString):
                return str(node)
            if not isinstance(node, Tag):
                return ""
            counter[0] += 1
            index = str(counter[0])
            if node.name in VOID_TAGS:
                marker = f"⟦html:{item_id}:void:{index}⟧"
                marker_map[marker] = str(node)
                return marker
            opening = f"⟦html:{item_id}:open:{index}⟧"
            closing = f"⟦html:{item_id}:close:{index}⟧"
            marker_map[opening] = HTMLMerger._tag_opening(node)
            marker_map[closing] = f"</{node.name}>"
            return opening + "".join(visit(child) for child in node.children) + closing

        return "".join(visit(child) for child in tag.children)

    @staticmethod
    def extract_semantic_nodes(html_content: str) -> Tuple[List[HTMLInputItem], BeautifulSoup]:
        soup = BeautifulSoup(html_content, "html.parser")
        items: List[HTMLInputItem] = []
        metadata: Dict[str, Dict[str, Any]] = {}
        node_index = 1

        semantic_roots = [
            tag for tag in soup.find_all(lambda candidate: candidate.name in SEMANTIC_BLOCK_TAGS)
            if not any(parent.name in SEMANTIC_BLOCK_TAGS for parent in tag.parents)
        ]
        semantic_root_ids = {id(tag) for tag in semantic_roots}

        for tag in semantic_roots:
            visible = HTMLMerger._visible_text(tag).strip()
            if not visible:
                continue
            item_id = f"node_{node_index}"
            node_index += 1
            marker_map: Dict[str, str] = {}
            protected = HTMLMerger._serialize_children(tag, item_id, marker_map)
            tag["data-node-id"] = item_id
            metadata[item_id] = {
                "kind": "tag",
                "target": tag,
                "marker_map": marker_map,
                "markers": tuple(marker_map),
            }
            items.append(HTMLInputItem(id=item_id, text=" ".join(visible.split()), protected_text=protected))

        # Capture text in div/section/body and other non-semantic containers,
        # including text separated by <br>.  Each NavigableString is a small,
        # stable unit so nested semantic blocks are never duplicated.
        for text_node in list(soup.find_all(string=True)):
            if not str(text_node).strip():
                continue
            parent = text_node.parent
            if not parent or parent.name in IGNORED_TEXT_PARENTS:
                continue
            if any(id(ancestor) in semantic_root_ids for ancestor in text_node.parents):
                continue
            item_id = f"node_{node_index}"
            node_index += 1
            visible = str(text_node).strip()
            metadata[item_id] = {"kind": "text", "target": text_node, "markers": ()}
            items.append(HTMLInputItem(id=item_id, text=" ".join(visible.split())))

        # BeautifulSoup permits application attributes, which keeps metadata
        # out of the serialized document and avoids fragile global lookups.
        soup._html_merger_metadata = metadata
        return items, soup

    @staticmethod
    def _validate_markers(item_id: str, translated: str, expected: Tuple[str, ...], marker_map: Dict[str, str]) -> None:
        found = [match.group(0) for match in MARKER_RE.finditer(translated)]
        all_markers = ANY_MARKER_RE.findall(translated)
        if (
            len(all_markers) != len(found)
            or any(marker not in expected for marker in all_markers)
            or set(found) != set(expected)
            or len(found) != len(expected)
        ):
            raise HTMLMarkerValidationError(
                f"HTML node {item_id} thiếu, thừa hoặc trùng marker bảo vệ."
            )
        stack: List[str] = []
        for marker in found:
            match = MARKER_RE.fullmatch(marker)
            if not match:
                raise HTMLMarkerValidationError(f"HTML node {item_id} chứa marker không hợp lệ.")
            kind = match.group(2)
            if kind == "open":
                stack.append(marker)
            elif kind == "close":
                if not stack:
                    raise HTMLMarkerValidationError(f"HTML node {item_id} sai thứ tự nesting marker.")
                opening = stack.pop()
                open_match = MARKER_RE.fullmatch(opening)
                if not open_match or open_match.group(3) != match.group(3):
                    raise HTMLMarkerValidationError(f"HTML node {item_id} sai cặp marker HTML.")
        if stack:
            raise HTMLMarkerValidationError(f"HTML node {item_id} còn marker mở chưa đóng.")

    @staticmethod
    def _replace_unmarked_text(tag: Tag, translated: str) -> None:
        text_nodes = [node for node in tag.find_all(string=True) if node.parent.name not in IGNORED_TEXT_PARENTS]
        if not text_nodes:
            tag.append(translated)
            return
        first = text_nodes[0]
        leading = re.match(r"^\s*", str(first)).group(0)
        trailing = re.search(r"\s*$", str(first)).group(0)
        first.replace_with(f"{leading}{translated.strip()}{trailing}")
        for node in text_nodes[1:]:
            node.replace_with("")

    @staticmethod
    def reconstruct_html(
        soup: BeautifulSoup,
        translations: List[HTMLTranslationItem],
        *,
        strict_markers: bool = False,
    ) -> str:
        trans_map: Dict[str, str] = {item.id: item.text_vi for item in translations}
        metadata: Dict[str, Dict[str, Any]] = getattr(soup, "_html_merger_metadata", {})
        if strict_markers and set(trans_map) != set(metadata):
            raise HTMLMarkerValidationError("HTML translation thiếu hoặc thừa node ID.")

        for item_id, node_info in metadata.items():
            if item_id not in trans_map:
                if strict_markers:
                    raise HTMLMarkerValidationError(f"HTML translation thiếu node {item_id}.")
                continue
            translated = trans_map[item_id]
            target = node_info["target"]
            if node_info["kind"] == "text":
                if isinstance(target, NavigableString):
                    leading = re.match(r"^\s*", str(target)).group(0)
                    trailing = re.search(r"\s*$", str(target)).group(0)
                    target.replace_with(f"{leading}{translated.strip()}{trailing}")
                continue
            marker_map = node_info["marker_map"]
            expected = node_info["markers"]
            has_markers = bool(MARKER_RE.search(translated))
            if strict_markers:
                HTMLMerger._validate_markers(item_id, translated, expected, marker_map)
            if has_markers and expected:
                HTMLMerger._validate_markers(item_id, translated, expected, marker_map)
                parts: List[str] = []
                cursor = 0
                for match in MARKER_RE.finditer(translated):
                    parts.append(html_lib.escape(translated[cursor:match.start()]))
                    parts.append(marker_map[match.group(0)])
                    cursor = match.end()
                parts.append(html_lib.escape(translated[cursor:]))
                fragment = BeautifulSoup("<div>" + "".join(parts) + "</div>", "html.parser").div
                target.clear()
                for child in list(fragment.contents):
                    target.append(child)
            else:
                if strict_markers and expected:
                    raise HTMLMarkerValidationError(f"HTML node {item_id} mất marker bảo vệ.")
                HTMLMerger._replace_unmarked_text(target, translated)
            target.attrs.pop("data-node-id", None)

        # Compatibility for callers that hand-build a soup/translations list.
        for tag in soup.find_all(lambda candidate: candidate.has_attr("data-node-id")):
            item_id = tag.get("data-node-id")
            if item_id in trans_map and item_id not in metadata:
                HTMLMerger._replace_unmarked_text(tag, trans_map[item_id])
            tag.attrs.pop("data-node-id", None)
        return str(soup)


__all__ = ["HTMLMerger", "HTMLMarkerValidationError"]
