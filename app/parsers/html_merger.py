from typing import List, Tuple, Dict
from bs4 import BeautifulSoup, NavigableString, Tag
from app.schemas.translation import HTMLInputItem, HTMLTranslationItem

SEMANTIC_BLOCK_TAGS = {"p", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "td", "th", "figcaption"}
MEDIA_TAGS = {"img", "svg", "video", "audio", "iframe"}


class HTMLMerger:
    """
    Trích xuất và gộp các text node thuộc cùng một khối ngữ nghĩa HTML (<p>, <li>, <blockquote>, v.v.).
    Giúp tránh việc xé 1 câu thành 2 id khi có thẻ inline (<em>, <b>, <span>, <a>).
    Bảo vệ các thẻ media (<img>, <svg>) khi tái tạo HTML.
    """

    @staticmethod
    def extract_semantic_nodes(html_content: str) -> Tuple[List[HTMLInputItem], BeautifulSoup]:
        """
        Trích xuất danh sách HTMLInputItem từ html_content.
        Trả về (items, soup_tree) để tái tạo HTML sau khi dịch.
        """
        soup = BeautifulSoup(html_content, "html.parser")
        items: List[HTMLInputItem] = []
        
        node_index = 1
        blocks = soup.find_all(lambda tag: tag.name in SEMANTIC_BLOCK_TAGS)
        
        if not blocks and soup.body:
            blocks = [soup.body]
        elif not blocks:
            blocks = [soup]

        for tag in blocks:
            # Bỏ qua nếu tag này nằm bên trong một block tag khác đã được xử lý (tránh lặp)
            if any(parent.name in SEMANTIC_BLOCK_TAGS for parent in tag.parents):
                continue

            raw_text = tag.get_text().strip()
            if not raw_text:
                continue

            item_id = f"node_{node_index}"
            node_index += 1
            
            tag["data-node-id"] = item_id
            items.append(HTMLInputItem(id=item_id, text=raw_text))

        return items, soup

    @staticmethod
    def reconstruct_html(soup: BeautifulSoup, translations: List[HTMLTranslationItem]) -> str:
        """
        Thay thế text đã dịch vào soup tree và bảo vệ thẻ media (<img>, <svg>).
        """
        trans_map: Dict[str, str] = {item.id: item.text_vi for item in translations}

        for tag in soup.find_all(lambda t: t.has_attr("data-node-id")):
            item_id = tag["data-node-id"]
            if item_id in trans_map:
                translated_text = trans_map[item_id]
                del tag["data-node-id"]

                # Tìm và giữ lại các thẻ media (<img>, <svg>, <video>) nếu có bên trong block
                media_elements = [child.extract() for child in tag.find_all(lambda elem: elem.name in MEDIA_TAGS)]
                
                # Gán chuỗi dịch mới
                tag.string = translated_text

                # Append lại các thẻ media vào cuối block tag
                for media_elem in media_elements:
                    tag.append(media_elem)
            else:
                del tag["data-node-id"]

        return str(soup)
