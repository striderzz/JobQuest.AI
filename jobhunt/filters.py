"""Keep-or-drop rules: India/remote location, SDE/ML role, entry level, recency.

The cheap checks (title, location, date) run before any per-posting detail
fetch; the level check needs the description, so it runs after.
"""
import re

INDIA_PLACES = (r"\bindia\b|\bind\b|bengaluru|bangalore|hyderabad|secunderabad|\bpune\b|chennai|gurugram|gurgaon|noida|"
                r"greater noida|mumbai|navi mumbai|thane|new delhi|\bdelhi\b|\bncr\b|kolkata|ahmedabad|gandhinagar|"
                r"kochi|cochin|trivandrum|thiruvananthapuram|jaipur|chandigarh|mohali|coimbatore|indore|mysuru|mysore|"
                r"bhubaneswar|nagpur|vadodara|visakhapatnam|vizag|lucknow|surat|madurai|mangalore|mangaluru|goa|"
                r"karnataka|telangana|maharashtra|tamil nadu|haryana|uttar pradesh|kerala|west bengal|gujarat")
INDIA = re.compile(INDIA_PLACES, re.I)

SDE = re.compile(r"software|\bsde\b|\bswe\b|developer|back[- ]?end|front[- ]?end|full[- ]?stack|\bmts\b|\bamts\b|"
                 r"member of (?:the )?technical staff|programmer|\bsdet\b|devops|site reliability|\bsre\b|"
                 r"\b(?:platform|infrastructure|android|ios|mobile|web|cloud|backend|"
                 r"frontend|security|qa automation|test automation|data platform|compiler|graphics|game|gameplay) engineer", re.I)
# "AI" alone isn't enough ("QA Engineer - AI Startup" is a QA job): it must describe the role itself.
ML = re.compile(r"machine learning|\bml\b|\bai\b[\s/&-]+(?:[a-z]+[\s/&-]+)?(?:engineer|developer|scientist|researcher|"
                r"intern|ml)\b|artificial intelligence|deep learning|\bnlp\b|computer vision|"
                r"data scien|applied scien|research (?:engineer|scientist)|\bllms?\b|gen ?ai|generative|"
                r"data engineer|\bmlops\b|perception|speech", re.I)
STRONG = re.compile(r"software (?:development |dev )?engineer|software developer|\bsde\b|\bswe\b|machine learning|"
                    r"\bml engineer|\bai engineer|data scientist|applied scientist|research engineer|back[- ]?end|"
                    r"front[- ]?end|full[- ]?stack|\bdeveloper\b", re.I)
NEVER = re.compile(r"support engineer|technical support|sales|solutions? (?:engineer|architect|consultant)|customer|"
                   r"field engineer|pre-?sales|recruit|marketing|teacher|tutor|trainer|annotator|writer", re.I)
NOT_ENGINEERING = re.compile(
    r"verification|physical design|\brtl\b|\basic\b|analog|mechanical|electrical|layout engineer|hardware|"
    r"recruit|talent acquisition|marketing|account|finance|legal|audit|\bhr\b|human resources|business|"
    r"technical writer|implementation|consultant|operations|partner|advocate|evangelist|product manager|"
    r"program manager|project manager|designer|analyst|teacher|tutor|trainer|content|writer|annotator|"
    r"\bbpo\b|payroll|procurement|supply chain|warehouse|delivery associate|process (?:lead|associate)|"
    r"compliance|risk|fraud|investigator|executive|coordinator", re.I)
SENIOR = re.compile(r"senior|\bsr\b\.?|\blead\b|\bstaff\b|principal|manager|director|\bhead\b|architect|\bvp\b|"
                    r"president|chief|distinguished|fellow|expert|\b(?:ii|iii|iv)\b|\b(?:sde|swe|engineer|developer|"
                    r"scientist|level)[- ]?(?:[2-9])\b|smts|lmts|pmts|\bmts[- ]?(?:2|3|ii|iii)\b", re.I)
# Amazon's "L4" is SDE I (entry), so bare L-levels are deliberately not treated as senior.
ENTRY = re.compile(r"\bintern\b|\binterns\b|internship|new[- ]?grad|graduate|campus|university|fresher|freshers|"
                   r"entry[- ]?level|junior|\bjr\b\.?|trainee|apprentice|early[- ]career|college|\bamts\b|"
                   r"associate (?:software|engineer|data|ml|machine|ai|developer|scientist|applied)|"
                   r"\b(?:sde|swe|engineer|developer|scientist|analyst)[- ]?(?:i|1)\b|\bengineer i\b|\blevel 1\b|"
                   r"\b20(?:2[5-9])\b batch|class of 20", re.I)
INTERN = re.compile(r"\bintern\b|\binterns\b|internship|co-?op\b|apprentice", re.I)
JUNK = re.compile(r"rater|annotat|talent hub|talent pool|talent community|expression of interest|future position|"
                  r"future opportunit|general application|tasks for ai|ai evaluation|ai trainer|\bgig\b|freelance|"
                  r"visibility engineer|implementation intern|evergreen|open application", re.I)
PHD = re.compile(r"\bph\.?\s?d\b", re.I)

_NUM_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15}
YEARS = re.compile(r"(\d{1,2}|zero|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen)\s*\+?\s*"
                   r"(?:\(\d+\)\s*)?(?:plus\s*)?(?:(?:-|–|to|or)\s*(?:\d{1,2}|one|two|three|four|five|six|seven|eight|"
                   r"nine|ten)\s*\+?\s*)?(?:years?|yrs?)\b", re.I)


PREFERRED = re.compile(r"preferred (?:qualifications|skills|experience|requirements)|nice[- ]to[- ]have|good[- ]to[- ]have|"
                       r"bonus points|\bbonus\b|desirable|would be a plus|is a plus|added advantage|preferred:", re.I)
REQUIRED = re.compile(r"basic qualifications|minimum qualifications|required (?:qualifications|skills|experience)|"
                      r"\brequirements\b|what you(?:'ll| will)? need|must[- ]haves?|who you are|"
                      r"(?<!preferred )\bqualifications\b", re.I)


def years_required(text):
    """The experience the posting asks for, as the largest lower bound stated near "experience".

    "3+ years of software development experience ... 1+ years with SQL" means 3, not 1.
    Returns None when nothing is stated.
    """
    text = text or ""
    pref = [m.start() for m in PREFERRED.finditer(text)]
    req = [m.start() for m in REQUIRED.finditer(text)]
    found = []
    for m in YEARS.finditer(text):
        # Skip numbers under a "preferred / nice to have" heading: those aren't requirements.
        last_pref = max((p for p in pref if p < m.start()), default=-1)
        last_req = max((p for p in req if p < m.start()), default=-1)
        if last_pref > last_req:
            continue
        before = text[max(0, m.start() - 90): m.start()].lower()
        after = text[m.end(): m.end() + 90].lower()
        if "experience" not in before + after and "exp " not in after[:6]:
            continue
        if re.search(r"\b(?:old|age|aged|warranty|history|ago|founded|in business|anniversary)\b", after[:25]):
            continue
        n = m.group(1).lower()
        n = _NUM_WORDS.get(n, None) if not n.isdigit() else int(n)
        if n is not None and n < 25:
            found.append(n)
    return max(found) if found else None


def classify_location(location, title=""):
    """India only. Returns '' for an on-site/hybrid job in India, 'India' for remote-within-India, else None.

    A remote role counts only if India is named among the places it hires from.
    """
    if not INDIA.search(location or ""):
        return None
    remote = re.search(r"remote|work from home|wfh|anywhere|distributed", f"{location} {title}", re.I)
    return "India" if remote else ""


CITIES = [
    ("Bengaluru", r"bengaluru|bangalore"), ("Hyderabad", r"hyderabad|secunderabad"), ("Pune", r"\bpune\b"),
    ("Chennai", r"chennai"), ("Mumbai", r"mumbai|thane|navi mumbai"),
    ("Delhi NCR", r"gurugram|gurgaon|noida|new delhi|\bdelhi\b|\bncr\b|faridabad|ghaziabad"),
    ("Kolkata", r"kolkata"), ("Ahmedabad", r"ahmedabad|gandhinagar"), ("Kochi", r"kochi|cochin"),
    ("Thiruvananthapuram", r"trivandrum|thiruvananthapuram"), ("Jaipur", r"jaipur"), ("Chandigarh", r"chandigarh|mohali"),
    ("Coimbatore", r"coimbatore"), ("Indore", r"indore"), ("Mysuru", r"mysuru|mysore"), ("Bhubaneswar", r"bhubaneswar"),
    ("Nagpur", r"nagpur"), ("Vadodara", r"vadodara"), ("Visakhapatnam", r"visakhapatnam|vizag"),
]
_CITY_RX = [(name, re.compile(rx, re.I)) for name, rx in CITIES]


def india_cities(location):
    """Canonical Indian cities named in a location string, in order of appearance."""
    found = sorted(((m.start(), name) for name, rx in _CITY_RX for m in [rx.search(location or "")] if m))
    out = []
    for _, name in found:
        if name not in out:
            out.append(name)
    return out


def role_kind(title):
    """'ML', 'SDE' or None. A clearly technical title ("Software Engineer, Risk") survives words that would
    otherwise mark a business role; sales/support/solutions roles never do."""
    if JUNK.search(title) or NEVER.search(title):
        return None
    ml, sde = ML.search(title), SDE.search(title)
    if not (ml or sde):
        return None
    if NOT_ENGINEERING.search(title) and not STRONG.search(title):
        return None
    return "ML" if ml else "SDE"


def title_is_senior(title):
    return bool(SENIOR.search(title))


def level_of(job, max_years, allow_unstated=False, profile=None):
    """Decide (level label, min years) for a job, or None to drop it."""
    t = job.title
    if PHD.search(t) and (profile is None or profile.get("degree_level", 0) < 4):
        return None
    yrs = years_required(job.description)
    intern = bool(INTERN.search(t)) or "intern" in job.hints
    explicit = intern or bool(ENTRY.search(t)) or bool(job.hints & {"entry", "university"}) \
        or bool(re.search(r"\bfreshers?\b|new grad|recent graduates?|graduating in|0\s*-\s*[12] years", job.description, re.I))
    if yrs is not None and yrs > max_years:
        return None
    if not explicit and yrs is None and not allow_unstated:
        return None
    if intern:
        label = "Intern"
    elif explicit and not yrs:
        label = "New grad"
    elif yrs is None:
        label = "Level unstated"
    else:
        label = f"{yrs}+ yrs" if yrs else "0 yrs"
    return label, yrs


def norm(s):
    s = s.lower()
    s = re.sub(r"\b(?:inc|llc|ltd|limited|pvt|private|corp|corporation|technologies|technology|labs|software|"
               r"careers|job board|jobs|india|the)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def dedupe_key(job):
    title = re.sub(r"[-–(|]\s*(?:r-?|jr|req)?[\d-]{4,}\)?", " ", job.title)     # strip requisition ids
    title = re.sub(r"\((?:remote|hybrid|onsite|on-site)\)|-\s*remote\b", " ", title, flags=re.I)
    city = INDIA.search(job.location)
    place = (city.group(0).lower().replace("bangalore", "bengaluru").replace("gurgaon", "gurugram")
             if city and job.remote != "worldwide" else "remote")
    return f"{norm(job.company)}|{norm(title)}|{norm(place)}"
