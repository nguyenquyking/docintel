"""Text pools for synthetic contracts (English and Vietnamese). All names are fictional."""
from __future__ import annotations

EN_COMPANIES = [
    "Acme Analytics Inc.", "Northwind Data LLC", "Bluefin Logistics Ltd.", "Crescent Health Partners LLC",
    "Granite Peak Software Corp.", "Harborview Retail Group Inc.", "Ionic Payments Pte. Ltd.", "Juniper Labs GmbH",
    "Keystone Facilities LLC", "Meridian Freight Corp.", "Orchid Biotech Inc.", "Pinecrest Hospitality LLC",
    "Quarry Street Capital LP", "Redwood Cloud Services Inc.", "Silverline Manufacturing Co.",
    "Tidewater Energy Ltd.", "Umbra Security Solutions LLC", "Vantage Point Media Inc.", "Willow & Finch Design LLC",
    "Zephyr Robotics Corp.",
]
VI_COMPANIES = [
    "Công ty TNHH Thương mại Minh Long", "Công ty Cổ phần Công nghệ Sao Việt", "Công ty TNHH Dịch vụ Logistics Hải Âu",
    "Công ty Cổ phần Thực phẩm An Phú", "Công ty TNHH Giải pháp Phần mềm Trí Tuệ", "Công ty Cổ phần Bất động sản Hòa Bình Xanh",
    "Công ty TNHH Sản xuất Bao bì Tân Tiến", "Công ty Cổ phần Dược phẩm Hồng Hà", "Công ty TNHH Nội thất Gỗ Việt",
    "Công ty Cổ phần Vận tải Biển Đông Hải", "Công ty TNHH Tư vấn Đầu tư Thành Công", "Công ty Cổ phần Điện máy Phương Nam",
    "Công ty TNHH Nông sản Xanh Cao Nguyên", "Công ty Cổ phần Truyền thông Ánh Dương",
]
EN_PEOPLE = [
    ("Jane Miller", "Chief Executive Officer"), ("Robert Chen", "Chief Financial Officer"),
    ("Priya Natarajan", "General Counsel"), ("Daniel Okafor", "VP Operations"), ("Sofia Alvarez", "Managing Director"),
    ("Thomas Becker", "Head of Procurement"), ("Emily Watson", "Chief Operating Officer"),
    ("Marcus Lee", "Director of Finance"), ("Hannah Kowalski", "President"), ("Arjun Mehta", "Chief Technology Officer"),
]
VI_PEOPLE = [
    ("Nguyễn Văn An", "Giám đốc"), ("Trần Thị Bình", "Tổng Giám đốc"), ("Lê Hoàng Minh", "Phó Giám đốc"),
    ("Phạm Thu Hà", "Giám đốc Tài chính"), ("Hoàng Đức Thắng", "Giám đốc Điều hành"), ("Võ Ngọc Lan", "Tổng Giám đốc"),
    ("Đặng Quốc Huy", "Giám đốc"), ("Bùi Thị Thanh Tâm", "Phó Tổng Giám đốc"), ("Đỗ Minh Khoa", "Giám đốc Kinh doanh"),
]
VI_ADDRESSES = [
    "Số 12 Nguyễn Huệ, Phường Bến Nghé, Quận 1, Thành phố Hồ Chí Minh",
    "Tầng 8, Tòa nhà Sao Mai, 45 Láng Hạ, Quận Đống Đa, Hà Nội",
    "Lô B3, Khu công nghiệp Tân Bình, Quận Tân Phú, Thành phố Hồ Chí Minh",
    "Số 88 Bạch Đằng, Quận Hải Châu, Thành phố Đà Nẵng",
]
EN_LAWS = [
    ("the State of New York", "new york"), ("the State of Delaware", "delaware"),
    ("the State of California", "california"), ("Singapore", "singapore"), ("England and Wales", "england and wales"),
]

EN_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
           "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def en_words(n: int) -> str:
    if n < 20:
        return EN_ONES[n]
    if n < 100:
        return EN_TENS[n // 10] + ("" if n % 10 == 0 else "-" + EN_ONES[n % 10])
    rest = n % 100
    return EN_ONES[n // 100] + " hundred" + ("" if rest == 0 else " " + en_words(rest))


VI_DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]


def vi_words(n: int) -> str:
    """Vietnamese reading of 0..999 (đọc số): 15 'mười lăm', 21 'hai mươi mốt', 24 'hai mươi tư', 105 'một trăm linh năm'."""
    if n < 10:
        return VI_DIGITS[n]
    if n < 20:
        return "mười" if n == 10 else "mười " + ("lăm" if n % 10 == 5 else VI_DIGITS[n % 10])
    if n < 100:
        unit = n % 10
        tail = "" if unit == 0 else " " + {1: "mốt", 4: "tư", 5: "lăm"}.get(unit, VI_DIGITS[unit])
        return VI_DIGITS[n // 10] + " mươi" + tail
    rest = n % 100
    head = VI_DIGITS[n // 100] + " trăm"
    if rest == 0:
        return head
    return head + (" linh " + VI_DIGITS[rest] if rest < 10 else " " + vi_words(rest))


EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December"]


def ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# Filler sentences: realistic boilerplate that carries no extracted fact.
EN_FILLER = {
    "Definitions": [
        "\"Affiliate\" means any entity that directly or indirectly controls, is controlled by, or is under common control with a party.",
        "\"Business Day\" means any day other than a Saturday, Sunday or public holiday in the place of performance.",
        "\"Confidential Information\" means all non-public information disclosed by one party to the other, in any form.",
        "\"Deliverables\" means all documents, software and materials provided by {provider} in the course of the Services.",
        "\"Personal Data\" has the meaning given in applicable data protection legislation.",
        "\"Service Levels\" means the performance standards set out in the applicable Schedule.",
        "\"Intellectual Property Rights\" means patents, copyrights, trade marks, design rights, database rights and know-how.",
        "Headings are for convenience only and do not affect the interpretation of this Agreement.",
        "Words in the singular include the plural and vice versa, and \"including\" means including without limitation.",
    ],
    "Services": [
        "{provider} shall perform the Services with reasonable skill, care and diligence in accordance with good industry practice.",
        "{customer} shall provide {provider} with timely access to its premises, systems and personnel as reasonably required.",
        "Each change to the scope of the Services shall be documented in a written change request signed by both parties.",
        "{provider} shall assign suitably qualified personnel and shall replace any individual reasonably objected to by {customer}.",
        "The parties shall meet at least monthly to review the performance of the Services and any open issues.",
        "{provider} shall maintain complete and accurate records relating to the Services for the period required by law.",
        "Acceptance of each Deliverable shall occur upon written confirmation by {customer} that it conforms to the specification.",
    ],
    "Confidentiality": [
        "Each party shall keep the other party's Confidential Information strictly confidential and use it solely for the purposes of this Agreement.",
        "Confidential Information may be disclosed to employees and advisers who need to know it and are bound by equivalent obligations.",
        "The obligations in this Section do not apply to information that is or becomes publicly available other than through breach.",
        "Upon request, the receiving party shall promptly return or destroy all Confidential Information of the disclosing party.",
        "A party compelled by law to disclose Confidential Information shall, where lawful, give the other party prompt notice.",
    ],
    "Data Protection": [
        "Each party shall comply with all applicable data protection laws in connection with the processing of Personal Data.",
        "{provider} shall process Personal Data only on the documented instructions of {customer}.",
        "{provider} shall implement appropriate technical and organisational measures to protect Personal Data against unauthorised processing.",
        "{provider} shall notify {customer} without undue delay after becoming aware of a personal data breach.",
        "Sub-processors may be engaged only with the prior written authorisation of {customer}.",
    ],
    "Security": [
        "{provider} shall maintain an information security programme aligned with recognised industry standards.",
        "Access to {customer} systems shall be limited to named personnel using multi-factor authentication.",
        "{provider} shall perform vulnerability scans at least quarterly and remediate critical findings promptly.",
        "Security incidents affecting {customer} data shall be reported in accordance with the incident response plan.",
        "Encryption shall be applied to {customer} data in transit and at rest using industry-standard algorithms.",
    ],
    "Intellectual Property": [
        "Each party retains all Intellectual Property Rights it owned before the Effective Date.",
        "{provider} grants {customer} a non-exclusive licence to use the Deliverables for its internal business purposes.",
        "Nothing in this Agreement transfers ownership of any pre-existing materials of either party.",
        "{customer} shall not reverse engineer, decompile or disassemble any software provided under this Agreement.",
    ],
    "Warranties": [
        "Each party warrants that it has full power and authority to enter into and perform this Agreement.",
        "{provider} warrants that the Services will be performed in a professional and workmanlike manner.",
        "Except as expressly stated, all warranties, conditions and representations are excluded to the extent permitted by law.",
        "{provider} warrants that the Deliverables will not infringe the Intellectual Property Rights of any third party.",
    ],
    "Indemnification": [
        "Each party shall indemnify the other against losses arising from its breach of the confidentiality obligations.",
        "{provider} shall defend {customer} against any claim that the Deliverables infringe a third party's rights.",
        "The indemnified party shall give prompt notice of any claim and reasonable cooperation in its defence.",
        "The indemnifying party shall have sole control of the defence and settlement of any indemnified claim.",
    ],
    "Compliance": [
        "Each party shall comply with all applicable anti-bribery, anti-corruption and sanctions laws.",
        "{provider} shall maintain policies and procedures reasonably designed to ensure compliance with this Section.",
        "Each party shall promptly report any request for an improper payment made in connection with this Agreement.",
        "{provider} shall ensure that its subcontractors comply with obligations equivalent to those in this Section.",
    ],
    "Audit": [
        "{customer} may audit {provider}'s compliance with this Agreement on reasonable prior notice during business hours.",
        "Audits shall be conducted no more than once in any twelve-month period except following a security incident.",
        "{provider} shall provide reasonable assistance and access to relevant records for the purpose of any audit.",
    ],
    "Force Majeure": [
        "Neither party shall be liable for any failure or delay caused by events beyond its reasonable control.",
        "The affected party shall promptly notify the other party and use reasonable efforts to mitigate the effect of the event.",
        "Force majeure events include natural disasters, epidemics, acts of government, war and failures of public utilities.",
    ],
    "Notices": [
        "Notices under this Agreement shall be in writing and delivered by hand, courier or e-mail to the addresses set out above.",
        "A notice delivered by courier is deemed received two (2) Business Days after dispatch.",
        "Either party may change its address for notices by giving written notice to the other party.",
    ],
    "Miscellaneous": [
        "This Agreement constitutes the entire agreement between the parties and supersedes all prior understandings.",
        "No amendment of this Agreement is effective unless it is in writing and signed by both parties.",
        "If any provision is held invalid, the remaining provisions shall continue in full force and effect.",
        "Neither party may assign this Agreement without the prior written consent of the other party.",
        "This Agreement may be executed in counterparts, each of which is an original and all of which form one instrument.",
        "No failure or delay in exercising any right operates as a waiver of that right.",
    ],
}
EN_FILLER_TOPICS_LONG = ["Security", "Data Protection", "Audit", "Compliance", "Intellectual Property", "Warranties",
                         "Indemnification", "Force Majeure"]

VI_FILLER = {
    "Giải thích từ ngữ": [
        "\"Thông tin bảo mật\" là mọi thông tin không công khai do một Bên cung cấp cho Bên kia dưới bất kỳ hình thức nào.",
        "\"Ngày làm việc\" là các ngày trong tuần trừ thứ Bảy, Chủ nhật và ngày nghỉ lễ theo quy định của pháp luật.",
        "\"Sản phẩm bàn giao\" là toàn bộ tài liệu, phần mềm và kết quả công việc do {provider} cung cấp theo Hợp đồng.",
        "\"Dữ liệu cá nhân\" có nghĩa như được quy định tại pháp luật về bảo vệ dữ liệu cá nhân.",
        "Các tiêu đề trong Hợp đồng chỉ nhằm thuận tiện cho việc tham chiếu và không ảnh hưởng đến việc giải thích Hợp đồng.",
    ],
    "Quyền và nghĩa vụ của các Bên": [
        "{provider} có nghĩa vụ thực hiện công việc đúng chất lượng, số lượng và thời hạn đã thỏa thuận.",
        "{customer} có trách nhiệm cung cấp kịp thời thông tin, tài liệu và phương tiện cần thiết để {provider} thực hiện công việc.",
        "Mọi thay đổi về phạm vi công việc phải được hai Bên lập thành văn bản và cùng ký xác nhận.",
        "{provider} phải bố trí nhân sự có đủ năng lực và chuyên môn phù hợp để thực hiện Hợp đồng.",
        "Hai Bên có trách nhiệm phối hợp, trao đổi thông tin thường xuyên để bảo đảm việc thực hiện Hợp đồng được thuận lợi.",
        "{customer} có quyền kiểm tra, giám sát việc thực hiện công việc nhưng không được làm ảnh hưởng đến hoạt động bình thường của {provider}.",
    ],
    "Bảo mật thông tin": [
        "Mỗi Bên cam kết giữ bí mật mọi Thông tin bảo mật nhận được từ Bên kia và chỉ sử dụng cho mục đích thực hiện Hợp đồng.",
        "Thông tin bảo mật chỉ được tiết lộ cho người lao động và cố vấn có nhu cầu cần biết và chịu nghĩa vụ bảo mật tương đương.",
        "Nghĩa vụ bảo mật không áp dụng đối với thông tin đã được công bố công khai không do lỗi của Bên nhận thông tin.",
        "Khi có yêu cầu, Bên nhận thông tin phải hoàn trả hoặc hủy toàn bộ Thông tin bảo mật của Bên cung cấp.",
    ],
    "Bảo vệ dữ liệu cá nhân": [
        "Mỗi Bên tuân thủ các quy định của pháp luật về bảo vệ dữ liệu cá nhân trong quá trình thực hiện Hợp đồng.",
        "{provider} chỉ xử lý dữ liệu cá nhân theo hướng dẫn bằng văn bản của {customer}.",
        "{provider} áp dụng các biện pháp kỹ thuật và tổ chức phù hợp để bảo vệ dữ liệu cá nhân khỏi việc truy cập trái phép.",
        "{provider} thông báo cho {customer} ngay khi phát hiện sự cố rò rỉ dữ liệu cá nhân.",
    ],
    "Bất khả kháng": [
        "Sự kiện bất khả kháng là sự kiện xảy ra một cách khách quan, không thể lường trước và không thể khắc phục được.",
        "Bên bị ảnh hưởng bởi sự kiện bất khả kháng phải thông báo cho Bên kia trong thời gian sớm nhất có thể.",
        "Các Bên không phải chịu trách nhiệm đối với việc chậm thực hiện nghĩa vụ do sự kiện bất khả kháng gây ra.",
    ],
    "Giải quyết tranh chấp": [
        "Mọi tranh chấp phát sinh từ Hợp đồng trước hết được giải quyết thông qua thương lượng, hòa giải giữa các Bên.",
        "Trường hợp không thương lượng được, tranh chấp được giải quyết tại Trung tâm Trọng tài Quốc tế Việt Nam.",
        "Phán quyết của trọng tài là chung thẩm và có giá trị ràng buộc các Bên.",
    ],
    "Điều khoản chung": [
        "Hợp đồng này thay thế mọi thỏa thuận trước đây giữa các Bên liên quan đến nội dung của Hợp đồng.",
        "Mọi sửa đổi, bổ sung Hợp đồng chỉ có hiệu lực khi được lập thành văn bản và có chữ ký của đại diện hợp pháp của hai Bên.",
        "Nếu bất kỳ điều khoản nào của Hợp đồng bị vô hiệu thì các điều khoản còn lại vẫn có hiệu lực.",
        "Không Bên nào được chuyển giao quyền và nghĩa vụ theo Hợp đồng cho bên thứ ba khi chưa có sự đồng ý của Bên kia.",
        "Hợp đồng được lập thành 02 (hai) bản có giá trị pháp lý như nhau, mỗi Bên giữ 01 (một) bản.",
    ],
}
VI_FILLER_TOPICS_LONG = ["Quyền và nghĩa vụ của các Bên", "Bảo mật thông tin", "Bảo vệ dữ liệu cá nhân", "Bất khả kháng",
                         "Giải quyết tranh chấp"]

LINE_ITEM_POOL_EN = [
    ("Platform subscription - Enterprise tier", 1, 48000), ("Implementation services", 1, 15000),
    ("Additional user licences", 50, 180), ("Data migration", 1, 6500), ("Premium support", 12, 950),
    ("Training workshop (per day)", 4, 2200), ("Custom integration development", 1, 12500),
    ("Hardware gateway unit", 10, 640), ("Annual maintenance", 1, 7200),
]
LINE_ITEM_POOL_VI = [
    ("Bản quyền phần mềm quản lý kho", 1, 180_000_000), ("Dịch vụ triển khai và cấu hình", 1, 95_000_000),
    ("Đào tạo người dùng (buổi)", 6, 7_500_000), ("Máy quét mã vạch cầm tay", 20, 4_850_000),
    ("Bảo trì hệ thống (tháng)", 12, 6_000_000), ("Chuyển đổi dữ liệu", 1, 42_000_000),
    ("Thùng carton 5 lớp", 5000, 12_500), ("Pallet nhựa tiêu chuẩn", 200, 650_000),
]
