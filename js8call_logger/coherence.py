"""How believable a decoded JS8 line looks, 0-100.

The decoder reports SNR, which measures the *signal*: a strong carrier with two
stations overlapping, or a distorted WebSDR stream, scores high and produces
nonsense. This measures the *text* instead, so the log can show both, the way
morse-logger shows q= (tone) next to coh= (text).

A token counts as real when it is a callsign, a grid locator, a short number, a
JS8 keyword/group (@ALLCALL) or an ordinary English word. Shape alone is not
evidence: a run of four letters is exactly what a mis-decode produces.
"""

from __future__ import annotations

import re

CALL_RE = re.compile(r"^[A-Z0-9]{1,3}[0-9][A-Z]{1,4}(?:/[A-Z0-9]{1,4})?$")
GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}(?:[A-X]{2})?$")
TOKEN_RE = re.compile(r"[A-Za-z0-9@/._-]+")

_EXTRA = ("via buro direct fer gud hi om yl xyl sk wkg wkd sri agn rpt "
          "hwu cpyl qsl qsy qrz qth qrm qsb pwr ant wx rig").split()

WORDS = frozenset("""
    "73 88 about above abt ack across after afternoon again agree alabama alaska all allcall "
    "almost alone along already also although always among and another answer ant antenna "
    "antennas any anyone anything apart are arizona arkansas around ask asked away back bad "
    "band bands beach beam beautiful because been before behind being below best better "
    "between beyond big bit bits bought boy bring brother brought btu build built buy bye "
    "california call called calling callsign came can care carolina carry change check child "
    "children choose church city class clear close cloudy cold colorado come coming community "
    "company complete computer connecticut consider continue control cool copy cost could "
    "country county course cover cpyl cq create current cut dakota data daughter day days de "
    "deal decide degrees delaware describe develop did die difference different difficult "
    "dipole dir direction discover distance do does doing done door down draw drive drop dry "
    "during early earth east eastern easy economic ed education effect eight either eleven "
    "else end energy english enough enter entire environment es especially even evening event "
    "ever every everyone everything evidence example expect experience explain eye face fact "
    "fall false family far fast father fb feel fer few field fifty figure fill final find "
    "fine finger finish finished fire first fish five floor florida follow food foot for "
    "force foreign form former forty forward four free frequency friend friends from front "
    "full fund future ga game garden gave ge general georgia get getting girl give given "
    "giving glass gm gn go goal goes going gone good goodbye got government grass great green "
    "grid ground group grow growth gud guess gun guy had hair half hampshire hand hang happen "
    "happy hard has hate have hawaii hb he head health hear heard heart heat heavy hello help "
    "helped her here herself hi high hill him himself his history hit hold home hope horse "
    "hospital hot hotel hour hours house how however hpe hr huge human hundred husband hw "
    "idaho idea identify if illinois image imagine impact important improve in include "
    "including increase indeed indiana individual industry info information inside instead "
    "interest international interview into investment involve iowa island issue it item its "
    "itself jersey job join just kansas keep kentucky kept key kid kids kill kind kitchen kn "
    "knew know knowledge known lake land language large last late later laugh law lawyer lay "
    "lead leader learn learned least leave left leg legal less let letter level lie life "
    "light like liked likely line list listen listening little live local long look looked "
    "looking lose loss lot lots louisiana love loved low made main maine maintain major make "
    "makes making man manage manager many market marriage maryland massachusetts material "
    "matter may maybe me mean measure media medical meet meeting member memory men mention "
    "message meter meters method mexico michigan middle might military million mind minnesota "
    "minute minutes miss mission mississippi missouri mode model modern moment money montana "
    "month months more morning most mother mouth move movement movie msg much music must my "
    "myself nack name names nation national natural nature near nearly nebraska necessary "
    "need needed network nevada never new news newspaper next nice night nine no noise none "
    "nor north northern not note nothing notice now number occur off offer office officer "
    "official often oh ohio oil ok okay oklahoma old om on once one only onto open operation "
    "opportunity option or order oregon organization other others our out outside over own "
    "owner page pain paint paper parent part participant particular particularly partner "
    "party pass past patient pattern pay peace pennsylvania people per perform performance "
    "perhaps period person personal phone physical pick picture piece place places plan plant "
    "play player please point police policy political poor popular population position "
    "positive possible power practice prepare present president pressure pretty prevent price "
    "private probably problem process produce product program project property protect prove "
    "provide pse public pull purpose push put pwr qrm qrz qsb qsl qso qsy qth qtr quality "
    "question quickly quite r race radio rainy raise range rate rather reach read ready real "
    "reality realize really reason receive received recent recently recognize record red "
    "reduce reflect region relate relationship relay religious remain remember remove report "
    "represent require research resource respond response responsibility rest result return "
    "reveal rhode rich rig right rise risk river road rock roger role room rr rule run safe "
    "said same sang save saw say says scene school science score sea season seat second "
    "section security see seeing seek seem seen sell send senior sense sent series serious "
    "serve service set seven several shake shall share she shoot short should shoulder show "
    "side sign signal signals significant similar simple simply since sing single sister sit "
    "site sitrep situation six sixty size sk skill skin small smile snowy snr so social "
    "society soldier some somebody someone something sometimes son song soon sorry sort sound "
    "source south southern space speak special specific speech spend sport spring staff stage "
    "stand standard star start started state statement static station status stay step still "
    "stock stop store story strategy street strong structure student study stuff style "
    "subject success such suddenly suffer suggest summer sunny support sure surface system "
    "table take taken taking talk talked talking task taught tax teach teacher team "
    "technology television tell temperature ten tend tennessee term test texas than thank "
    "thanks that the their them themselves then theory there these they thing things think "
    "third thirty this those though thought thousand threat three through throughout throw "
    "thus time times tnx to today together told tomorrow tonight too took top total tough "
    "toward tower towers town trade traditional training travel treat treatment tree trees "
    "trial tried trip trouble true truth try trying tu turn tv twelve twenty two type under "
    "understand unit until up upon ur us use used using usually utah value various vermont "
    "very victim view violence virginia visit voice vote wait waiting wake walk wall want "
    "wanted war warm was washington watch water watt watts way ways we weapon wear weather "
    "week weeks weight well went were west western wet what whatever when where whether which "
    "while white who whole whom whose why wide wife will win wind window windy wire wires "
    "wisconsin wish with within without woman women wonder woods word work worked worker "
    "working world worry worse worst would write writer wrong wrote wx wyoming xyl yard yeah "
    "year years yes yesterday yet yl york you young your yourself "
""".split()) | frozenset(_EXTRA)


def coherence(text: str) -> int:
    """0-100: proportion of tokens that look like real JS8 traffic."""
    if not text:
        return 0
    toks = [t for t in TOKEN_RE.findall(text) if t.strip(".,:;!?()[]{}<>\u2662")]
    if not toks:
        return 0
    good = 0
    for raw in toks:
        t = raw.strip(".,:;!?()[]{}<>\u2662")
        if not t:
            continue
        up = t.upper()
        if t.startswith("@"):
            good += 1
        elif CALL_RE.match(up) or GRID_RE.match(up):
            good += 1
        elif t.isdigit() and len(t) <= 4:
            good += 1
        elif t.lower() in WORDS:
            good += 1
    return round(100 * good / len(toks))
