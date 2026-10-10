"""Benchmark cases built from five real EduNXT university lists (consultant-made, Sept 2026).

The student's curriculum and grades are not on those lists, so each case states its assumption.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .agents.schemas import StudentProfile


@dataclass
class BenchmarkCase:
    id: str
    title: str
    messages: list[str]  # what the student sends on WhatsApp (first enquiry, then profile)
    profile: StudentProfile
    consultant_picks: list[tuple[str, str]]  # (university, course) on EduNXT's own list
    revision: str = "Remove the most expensive university and add one cheaper alternative in the same country."
    assumptions: str = ""
    list_size: int = field(default=0)

    def __post_init__(self):
        self.list_size = self.list_size or len(self.consultant_picks)


CASES = [
    BenchmarkCase(
        id="canada_ee",
        title="Khaled: Electrical Engineering, Canada",
        messages=[
            "salam 3alekom, ana Khaled. 3ayez a3raf ezay te2daro tesa3dony adros barra?",
            "ana fe IB, predicted 36 (HL Maths AA 6, Physics 6, Chemistry 6). 3ayez adros electrical engineering bachelor fe Canada, start September 2027",
        ],
        profile=StudentProfile(
            student_name="Khaled",
            curriculum="IB",
            predicted_grades="IB predicted 36 (HL Maths AA 6, Physics 6, Chemistry 6)",
            intended_course="Electrical Engineering",
            degree_level="Bachelor's",
            countries=["Canada"],
            intake="September 2027",
            nationality="Egyptian",
        ),
        consultant_picks=[
            ("University of Toronto", "BASc Electrical & Computer Engineering"),
            ("McGill University", "BEng Electrical Engineering"),
            ("University of British Columbia (UBC)", "BASc Electrical Engineering"),
            ("University of Alberta", "BSc Electrical Engineering"),
            ("University of Waterloo", "BASc Electrical Engineering"),
            ("McMaster University", "BEng Electrical Engineering"),
            ("University of Ottawa", "BASc Electrical Engineering"),
            ("Queen's University", "BASc Electrical Engineering"),
            ("Western University", "BESc Electrical Engineering"),
            ("University of Calgary", "BSc Electrical Engineering"),
        ],
        assumptions="Curriculum, grades, intake and nationality assumed (not on EduNXT's list).",
    ),
    BenchmarkCase(
        id="europe_ee",
        title="Khaled: Electrical Engineering, Europe",
        messages=[
            "Hi, I'm Khaled. Can you help me apply to universities in Europe?",
            "IB predicted 36, HL Maths AA 6, Physics 6, Chemistry 6. Bachelor's in electrical engineering anywhere in Europe, Sept 2027",
        ],
        profile=StudentProfile(
            student_name="Khaled",
            curriculum="IB",
            predicted_grades="IB predicted 36 (HL Maths AA 6, Physics 6, Chemistry 6)",
            intended_course="Electrical Engineering",
            degree_level="Bachelor's",
            countries=["Europe"],
            intake="September 2027",
            nationality="Egyptian",
        ),
        consultant_picks=[
            ("Politecnico di Milano", "Engineering Science BSc"),
            ("Aalto University", "Digital Systems & Design BSc"),
            ("TU Eindhoven", "Electrical Engineering BSc"),
            ("University of Twente", "Electrical Engineering BSc"),
            ("Trinity College Dublin", "Electronic Engineering"),
            ("University College Cork", "Electrical & Electronic Engineering"),
            ("University of Galway", "Electrical & Electronic Engineering"),
            ("LUT University", "Electrical Engineering BSc"),
            ("Aalto University", "Computer Engineering BSc"),
            ("Politecnico di Torino", "Electronic & Communications Engineering BSc"),
            ("Brno University of Technology", "Electrical Engineering BSc"),
            ("Kaunas University of Technology", "Electronics & Electrical Engineering BEng"),
            ("POLITEHNICA Bucharest", "Electrical Engineering & Computers"),
        ],
        assumptions="Curriculum, grades, intake and nationality assumed (not on EduNXT's list).",
    ),
    BenchmarkCase(
        id="events_uk_eu",
        title="Aya Sobhy: Events Management, Spain / Germany / UK",
        messages=[
            "السلام عليكم، عايزة أعرف تفاصيل عن خدماتكم للدراسة برا",
            "اسمي آية صبحي، American Diploma GPA 3.5، عايزة أدرس Events Management بكالوريوس في إسبانيا أو ألمانيا أو إنجلترا",
        ],
        profile=StudentProfile(
            student_name="Aya Sobhy",
            curriculum="American Diploma",
            predicted_grades="GPA 3.5",
            intended_course="Events Management",
            degree_level="Bachelor's (BA / BSc)",
            countries=["Spain", "Germany", "United Kingdom"],
            nationality="Egyptian",
        ),
        consultant_picks=[
            ("Universidad Europea", "Bachelor's in Tourism and Leisure Management"),
            ("BSBI (Berlin School of Business and Innovation)", "BA (Hons) Tourism and Hospitality Management"),
            ("Schiller International University", "BSc International Hospitality and Tourism Management"),
            ("University of Surrey", "BSc (Hons) International Event Management"),
            ("Manchester Metropolitan University", "BA (Hons) Events Management"),
            ("Nottingham Trent University", "Event Management BA (Hons)"),
            ("University of Essex", "BA (Hons) Events Management"),
            ("UWE Bristol", "BA (Hons) Business and Events Management"),
            ("EU Business School", "BA Leisure & Tourism Management"),
        ],
        assumptions="Curriculum and grades assumed (not on EduNXT's list).",
    ),
    BenchmarkCase(
        id="marketing_eu",
        title="Aya Sobhy: Marketing, Spain / Germany / Switzerland",
        messages=[
            "hi, momken a3raf el services beta3etkom?",
            "ana Aya, American Diploma GPA 3.5, 3ayza adros marketing bachelor fe Spain aw Germany aw Switzerland",
        ],
        profile=StudentProfile(
            student_name="Aya Sobhy",
            curriculum="American Diploma",
            predicted_grades="GPA 3.5",
            intended_course="Marketing",
            degree_level="Bachelor's (BA / BSc)",
            countries=["Spain", "Germany", "Switzerland"],
            nationality="Egyptian",
        ),
        consultant_picks=[
            ("EU Business School", "BA (Hons) Business Management and Marketing"),
            ("GBSB Global Business School", "Business Bachelor with Digital Marketing"),
            ("Saint Louis University", "Marketing, B.S.B.A."),
            ("Universidad Europea", "Bachelor's Degree in Marketing"),
            ("Geneva Business School", "Digital Marketing"),
            ("BSBI (Berlin School of Business and Innovation)", "BA (Hons) Media and Communications"),
            ("Gisma University of Applied Sciences", "BSc Business Management & Marketing"),
        ],
        assumptions="Curriculum and grades assumed (not on EduNXT's list).",
    ),
    BenchmarkCase(
        id="medicine_eu",
        title="Lucas Maldonado: Medicine, Italy / Bulgaria, under $10k",
        messages=[
            "Hello! I'm interested in studying medicine in Europe, how does your service work?",
            "I'm Lucas, IB predicted 34 with HL Biology 6 and Chemistry 6. Looking for English-taught medicine in Italy (first choice) or Bulgaria, budget under $10,000 USD per year",
        ],
        profile=StudentProfile(
            student_name="Lucas Maldonado",
            curriculum="IB",
            predicted_grades="IB predicted 34 (HL Biology 6, Chemistry 6)",
            intended_course="Medicine (MBBS / MD)",
            degree_level="Undergraduate-entry Medicine",
            countries=["Italy", "Bulgaria"],
            budget="Under $10,000 USD per year",
        ),
        consultant_picks=[
            ("University of Milan (La Statale)", "Medicine & Surgery – International Medical School (6 yrs, English)"),
            ("University of Pavia", "Medicine & Surgery – Harvey Course (6 yrs, English)"),
            ("University of Bologna", "Medicine & Surgery (6 yrs, English)"),
            ("University of Padua", "Medicine & Surgery (6 yrs, English)"),
            ("Sapienza University of Rome", "Medicine & Surgery (6 yrs, English)"),
            ("University of Naples Federico II", "Medicine & Surgery (6 yrs, English)"),
            ("University of Catania", "Medicine & Surgery (6 yrs, English)"),
            ("Trakia University", "Medicine (6 yrs, English)"),
        ],
        assumptions="Grades assumed (EduNXT's list implies IB).",
    ),
]
