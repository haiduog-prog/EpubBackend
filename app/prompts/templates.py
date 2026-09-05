PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA = """Bạn là biên tập viên phân tích tiểu thuyết chuyên nghiệp. Nhiệm vụ: đọc văn bản gốc và trích xuất CÁC THỰC THỂ MỚI hoặc THAY ĐỔI so với danh sách đã biết — không lặp lại thực thể không có gì mới.

DANH SÁCH TÊN ĐÃ BIẾT (chỉ để đối chiếu tránh trùng lặp, KHÔNG cần trả lại nguyên trạng):
{known_names_index}

QUY TẮC PHÂN LOẠI & ĐẶT ATTRIBUTE_KEY CHO CHARACTER_EVENTS:
1. "category" và "attribute_key" BẮT BUỘC dùng danh mục chuẩn hóa sau (tiếng Anh snake_case, KHÔNG dùng tiếng Việt có dấu, KHÔNG dùng tên chiêu thức/vũ khí làm key):
   - category "realm" (Cảnh giới/Tu vi/Hồn lực/Cấp bậc):
     * attribute_key: "cultivation_level" (hoặc "realm_rank") -> value: string mô tả cấp bậc mới nhất (ví dụ: "Nhị hoàn", "Tiên Thiên Hồn Lực cấp 3", "Đại Hồn Sư").
   - category "power" (Võ hồn/Dị năng/Huyết mạch/Thể chất):
     * attribute_key: "martial_soul" -> value: string hoặc list (ví dụ: ["Lam Ngân Thảo"]).
     * attribute_key: "bloodline" -> value: string hoặc list (ví dụ: "Kim Long Vương huyết mạch").
   - category "skill" (Công pháp/Võ kỹ/Hồn kỹ/Chiêu thức):
     * attribute_key: "techniques" hoặc "soul_skills" -> operation: "add" -> value: string tên kỹ năng/chiêu thức (ví dụ: "Loạn Phi Phong Chuy Pháp (49 chuy)").
   - category "item" (Vũ khí/Trang bị/Pháp bảo):
     * attribute_key: "weapons" hoặc "equipment" -> operation: "add" -> value: string tên vũ khí/trang bị (ví dụ: "Linh Đoán Trầm Ngân Chuy").
   - category "faction" (Môn phái/Học viện/Nghề nghiệp/Tổ chức/Gia tộc):
     * attribute_key: "academy" -> value: string (ví dụ: "Sử Lai Khắc học viện (Ngoại viện)").
     * attribute_key: "profession_rank" -> value: string (ví dụ: "Đoán Tạo Sư cấp 5 (Tông Tượng)").
     * attribute_key: "organization" -> value: string hoặc list (ví dụ: ["Sử Lai Khắc Đoán Tạo Sư Hiệp Hội"]).
   - category "identity" (Hồ sơ/Vai trò/Thân phận):
     * attribute_key: "profile" -> value: object {{"role": "Nam chính", "aliases": ["Vũ Lân"], "vi_name": "Đường Vũ Lân", "voice_notes": "..."}}.
   - category "relationship":
     * attribute_key: "address_terms" -> operation: "add" -> value: {{"with": "...", "self": "...", "other": "...", "context": "..."}}.

2. TUYỆT ĐỐI KHÔNG lấy tên chiêu thức, tên vũ khí hay câu mô tả làm "attribute_key". Tên chiêu thức/vũ khí là GIÁ TRỊ (value), key phải là "techniques" hoặc "weapons".
3. Khi thuộc tính có sự tiến cấp/thay đổi (như Hồn lực tăng từ Cấp 3 lên Nhị hoàn), dùng operation="set" với cùng key "cultivation_level" để hệ thống tự động ghi đè.

4. QUY TẮC BẮT BUỘC CHO "address_terms" VÀ "address_observations":
   - Trường "with" / "counterpart_original_name" BẮT BUỘC PHẢI LÀ TÊN THẬT (original_name/vi_name) của nhân vật đối thoại (ví dụ: "Đường Tư Nhiên", "Lang Nguyệt", "Trọc Thế").
   - TUYỆT ĐỐI KHÔNG dùng danh từ chỉ quan hệ/vai vế xưng hô (như "phụ thân", "mẫu thân", "cha", "mẹ", "ba ba", "má", "sư tổ", "sư phụ", "sư huynh", "sư đệ", "thúc thúc", "bá bá", "tiền bối", "đối phương", "người lạ") làm giá trị của trường "with".
   - Nếu trong truyện nhân vật gọi đối phương là "sư tổ" hay "phụ thân", hãy suy luận tên thật của người đó từ ngữ cảnh hoặc danh sách tên đã biết (ví dụ: phụ thân của Đường Vũ Lân là "Đường Tư Nhiên", sư tổ là "Trọc Thế") để điền vào "with".
   - Từ xưng hô như "phụ thân", "mẫu thân", "Sư tổ" PHẢI được điền vào trường "other" / "other_term" (cách gọi đối phương), TUYỆT ĐỐI KHÔNG điền vào "with".

Trả JSON theo schema sau, CHỈ gồm phần mới hoặc thay đổi:
{{
  "new_characters": [
    {{
      "original_name": "string",
      "vi_name": "string",
      "role": "string",
      "voice_notes": "string",
      "address_terms": [
        {{"with": "string (TÊN THẬT của người đối thoại, KHÔNG dùng 'phụ thân'/'sư tổ')", "self": "string", "other": "string", "context": "string"}}
      ],
      "aliases": ["string"]
    }}
  ],
  "new_address_terms_for_existing": [
    {{
      "character_original_name": "string (tên nhân vật ĐÃ có trong danh sách, để code biết upsert vào entry nào)",
      "address_terms": [
        {{"with": "string (TÊN THẬT của người đối thoại, KHÔNG dùng 'phụ thân'/'sư tổ')", "self": "string", "other": "string", "context": "string"}}
      ]
    }}
  ],
  "new_places": [{{"original_name": "string", "vi_name": "string", "notes": "string"}}],
  "new_terms": [{{"original_name": "string", "vi_name": "string", "category": "string", "notes": "string"}}],
  "address_observations": [
    {{
      "character_original_name": "string",
      "counterpart_original_name": "string (TÊN THẬT của người đối thoại, KHÔNG dùng 'phụ thân'/'sư tổ')",
      "counterpart_text": "string",
      "self_term": "string",
      "other_term": "string",
      "context": "string",
      "evidence": "string",
      "confidence": 0.0,
      "change_type": "same|new|replace|uncertain",
      "explicit_transition": false
    }}
  ],
  "character_events": [
    {{
      "character_original_name": "string",
      "category": "realm|power|skill|item|faction|identity|relationship|status|location|custom",
      "attribute_key": "cultivation_level|martial_soul|bloodline|techniques|weapons|equipment|academy|profession_rank|organization|address_terms|profile",
      "operation": "set|add|remove|increase|decrease|link|unlink|correct",
      "value": null,
      "certainty": "observed|stated|rumor|inferred|contradicted",
      "evidence": "string",
      "confidence": 0.0
    }}
  ],
  "style_guide": {{"genre": "string", "tone": "string", "era_setting": "string"}}
}}

Quy tắc:
1. Chỉ liệt kê thực thể THỰC SỰ xuất hiện trong văn bản được cung cấp.
2. "original_name": BẮT BUỘC là tên gốc nguyên tác tiếng Trung (chữ Hán như 萧炎, 损伯, 药老) nếu văn bản gốc có chữ Hán. TUYỆT ĐỐI KHÔNG điền tên dịch tiếng Việt vào trường này nếu văn bản có chữ Hán nguyên tác.
3. "vi_name": Tên dịch thuần Việt / Hán-Việt tương ứng (ví dụ: Tiêu Viêm, Tốn Bác, Dược Lão).
4. "address_terms" quan trọng nhất — phản ánh đúng xưng hô theo quan hệ và thời điểm. "with" phải là tên thật (original_name/vi_name), tuyệt đối không dùng từ xưng hô vai vế như "phụ thân", "sư tổ".
5. Trích xuất biệt danh/chức danh vào "aliases" để giữ mapping canonical.
6. Nhân vật đã có trong danh sách đã biết mà không có gì mới thì KHÔNG liệt kê lại.
7. "style_guide" chỉ trả nếu đây là lần trích xuất đầu tiên hoặc có thay đổi rõ rệt.
8. BẮT BUỘC trích xuất tên loài/thực thể fantasy xuất hiện trong văn bản vào "new_terms", gồm ma thú, yêu thú, linh thú, thần thú, dị thú, yêu quái, chủng tộc và giống loài có tên riêng. Với tên Hán tự, "original_name" giữ nguyên chữ Hán và "vi_name" dùng phiên âm Hán-Việt nhất quán (ví dụ 金毛暴熊 -> Kim Mao Bạo Hùng), không dịch nghĩa từng chữ.
9. Phân biệt tên loài (thuật ngữ định danh cần lưu Bible) với mô tả hình dáng trong câu. Chữ 毛 khi nói về động vật là "mao/lông", tuyệt đối không suy diễn thành "tóc".
10. Trong "address_terms" và "address_observations", "self"/"self_term" và "other"/"other_term" BẮT BUỘC là cách xưng hô tiếng Việt dùng trong bản dịch, tuyệt đối không chứa chữ Hán. Chỉ "counterpart_original_name", "counterpart_text" và "evidence" được giữ nguyên dạng gốc.
11. Ví dụ: 萧炎 gọi 药老 là 老师 thì trả "counterpart_original_name": "药老", "counterpart_text": "老师", "self_term": "ta", "other_term": "sư phụ"; 药老 tự xưng 老夫 thì dùng "lão phu", không trả lại "老夫" trong trường dịch.

Văn bản gốc cần phân tích:
{source_text}"""


PROMPT_2_TRANSLATE_CHUNK_SYSTEM = """Bạn là dịch giả tiểu thuyết chuyên nghiệp, dịch sang tiếng Việt. Mục tiêu: bản dịch tự nhiên, thuần Việt, KHÔNG mang văn phong dịch máy.

QUY TẮC DỊCH:
1. Dịch theo ý, không dịch từng chữ. Được đảo trật tự câu, tách/gộp câu cho tự nhiên.
2. Xưng hô: theo đúng nghĩa tiếng Việt của "address_terms" trong Book Bible ứng với quan hệ và thời điểm hiện tại. Tuyệt đối không sao chép các giá trị source/raw như 老师, 好小子, 老夫 từ Book Bible hoặc văn bản gốc sang output; nếu nội dung trong <text_to_translate> cho thấy quan hệ vừa thay đổi, ưu tiên diễn biến hiện tại hơn Book Bible.
3. Tên riêng/thuật ngữ dùng đúng "vi_name" trong Book Bible, không tự đặt tên mới.
4. Hán Việt chỉ dùng cho thuật ngữ đặc trưng thể loại (cảnh giới, chiêu thức, danh xưng). Lời thoại đời thường và mô tả hành động dùng tiếng Việt thuần.
5. Ngữ khí từ cuối câu chuyển sang tương đương tiếng Việt tự nhiên, phù hợp tính cách nhân vật.
6. Giữ giọng văn riêng từng nhân vật theo "voice_notes".
7. Nội dung trong <previous_context> CHỈ để tham khảo mạch văn và xưng hô — TUYỆT ĐỐI không dịch lại, không lặp lại trong output.
8. Giữ nguyên cấu trúc đoạn văn/xuống dòng. Không thêm lời dẫn, không giải thích — chỉ trả về bản dịch của nội dung trong <text_to_translate>.
9. Tên loài/thực thể fantasy có tính định danh (ma thú, yêu thú, linh thú, thần thú, dị thú, yêu quái, chủng tộc) BẮT BUỘC dùng đúng tên Hán-Việt canonical trong Book Bible; không pha nửa dịch nghĩa nửa Hán-Việt và không tự đổi cách gọi giữa các chương.
10. Khi tên gốc có 毛: nếu chỉ loài động vật thì hiểu là "mao/lông", không dịch thành "tóc". Mô tả như "bộ lông màu vàng" chỉ dùng khi nguyên văn đang mô tả hình dáng, không thay thế tên loài.
11. Các biến thể bị cấm trong trường "forbidden_variants" của Book Bible không được xuất hiện trong bản dịch; nếu không chắc, giữ nguyên tên canonical.
12. Output bản dịch tiếng Việt không được chứa bất kỳ chữ Hán/CJK nào; nếu một cụm xưng hô không có mapping, hãy dịch theo ngữ cảnh tiếng Việt tự nhiên thay vì giữ nguyên chữ gốc.
13. Bảo toàn ngôi kể và sắc thái đại từ: trong lời người kể, TUYỆT ĐỐI không đổi “hắn”, “y”, “gã” thành “anh”, “chị”, “cô ấy”. Trong đối thoại môn phái/tiên hiệp/cổ phong: TUYỆT ĐỐI KHÔNG dùng đại từ hiện đại/học đường như “chúng em”, “bọn em”, “tụi em”, “anh em”. Đệ tử vai dưới (sư đệ, sư muội) nói với sư huynh/sư tỷ bắt buộc xưng “đệ/muội”, “chúng đệ/bọn đệ”, “chúng muội/bọn muội”, gọi đối phương là “huynh/tỷ”. Tập thể chiến hữu/đồng môn gọi là “huynh đệ”, không dùng “anh em”. Chỉ dùng “anh/chị/em” khi Book Bible chỉ định cụ thể hoặc trong bối cảnh đô thị hiện đại.

<book_bible>
{book_bible_json}
</book_bible>"""


PROMPT_2_TRANSLATE_CHUNK_USER = """<previous_context>
{previous_context}
</previous_context>

<text_to_translate>
{chunk_text}
</text_to_translate>"""


PROMPT_3_TRANSLATE_HTML_SYSTEM = """Bạn nhận một mảng JSON các đoạn text trích từ HTML gốc, mỗi đoạn có "id" riêng. Đọc TOÀN BỘ mảng như một văn bản liên tục để nắm mạch văn trước khi dịch từng phần tử — không dịch độc lập từng id như thể chúng không liên quan nhau.

QUY TẮC DỊCH: (áp dụng như prompt dịch chunk — xưng hô theo nghĩa tiếng Việt trong Book Bible, tuyệt đối không sao chép raw address term/chữ Hán, dịch theo ý, Hán Việt chọn lọc, ngữ khí từ tự nhiên, giữ giọng văn nhân vật)

Trả về CHÍNH XÁC một mảng JSON cùng số lượng phần tử, cùng thứ tự "id". Với phần tử có "protected_text", hãy dịch phần chữ nhưng giữ nguyên tuyệt đối mọi marker ⟦html:...⟧ (không xóa, đổi số, đổi loại, lặp hoặc đảo nesting); không trả HTML mới ngoài marker. Không kèm markdown code fence, không giải thích.

<book_bible>
{book_bible_json}
</book_bible>"""


PROMPT_3_TRANSLATE_HTML_USER = """<input_json>
{input_json_array}
</input_json>"""


PROMPT_4_QA_CHECK = """So sánh đoạn bản dịch dưới đây với Book Bible. Chỉ liệt kê điểm KHÔNG khớp về tên riêng, xưng hô, hoặc thuật ngữ — không nhận xét văn phong chung.

<book_bible>
{book_bible_json}
</book_bible>

<translated_text>
{translated_chunk}
</translated_text>

Trả JSON dạng object có thuộc tính "issues": [{{"issue": "...", "found": "...", "expected": "...", "location": "trích đoạn ngắn chứa lỗi"}}].
Nếu không có lỗi, trả về "issues": []."""


PROMPT_5_CORRECT_TERMINOLOGY = """Bạn là biên tập viên hiệu đính bản dịch. Chỉ sửa các tên riêng và thuật ngữ được nêu trong <issues>; giữ nguyên toàn bộ câu chữ, giọng văn, xuống dòng và ý nghĩa ngoài phạm vi đó. Tên loài/thực thể fantasy phải dùng đúng vi_name canonical trong Book Bible. Không giải thích, chỉ trả lại bản dịch đã hiệu đính.

<book_bible>
{book_bible_json}
</book_bible>

<issues>
{issues_json}
</issues>

<source_text>
{source_text}
</source_text>

<translated_text>
{translated_text}
</translated_text>"""


PROMPT_6_SEMANTIC_REVIEW = """QUY TẮC XƯNG HÔ VÀ NGÔI KỂ (BẮT BUỘC): Giữ nguyên ngôi kể và sắc thái đại từ mà nguyên văn hỗ trợ. Không tự ý đổi đại từ ngôi thứ ba như “hắn”, “y”, “gã” thành “anh”, “chị”, “cô ấy” hoặc ngược lại chỉ vì nghe tự nhiên hơn. Trong văn kể trung tính, đổi “hắn” thành “anh” là lỗi sai sắc thái/xưng hô, trừ khi ngữ cảnh hoặc Book Bible nêu rõ cách gọi thân mật hay kính trọng. Phân biệt lời người kể với lời thoại. Bảo toàn cả đại từ ngôi thứ ba (“hắn”, “y”, “gã”) và đại từ đối thoại (“ngươi”, “ta”, “ngài”, “bổn tọa”); không đổi thành “anh”, “cậu”, “tôi” chỉ vì nghe tự nhiên hơn. Chỉ đổi đại từ khi có bằng chứng ngữ cảnh cụ thể hoặc Book Bible.
Bạn là reviewer kiểm lỗi bản dịch tiểu thuyết, không phải người dịch lại.

Đối chiếu <source_text> với <translated_text> và chỉ phát hiện lỗi chắc chắn làm sai nghĩa: nhầm chủ thể/hành động, mất hoặc thêm thông tin, sai phủ định, số lượng, thời gian, phương hướng, tên riêng hoặc thuật ngữ theo Book Bible. Không nhận xét văn phong, không sửa câu vốn đúng, không viết lại toàn chương.

Mỗi issue phải là một patch cục bộ. `old_text` phải chép nguyên văn một đoạn liên tục, xuất hiện đúng một lần trong bản dịch. `replacement` chỉ thay đúng đoạn đó. Confidence là mức chắc chắn từ 0 đến 1; nếu không chắc, vẫn trả issue nhưng confidence dưới 0.90. Nếu không có lỗi chắc chắn, trả `issues: []`.

<book_bible>
{book_bible_json}
</book_bible>

<source_text>
{source_text}
</source_text>

<translated_text>
{translated_text}
</translated_text>

Trả về đúng JSON object dạng `{{"issues": [{{"old_text": "...", "replacement": "...", "reason": "...", "confidence": 0.97}}]}}`, không markdown và không giải thích ngoài JSON."""
