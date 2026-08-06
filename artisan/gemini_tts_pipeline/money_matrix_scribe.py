import random
from pathlib import Path
from typing import Optional

OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "output" / "scripts"

INTRO_ANECDOTES = [
    "I remember sitting in my first finance class, watching half the room glaze over when the professor started talking about compound interest. But here's the thing — that boring math problem was quietly making millionaires.",
    "A few years ago, I sat down with a friend who was drowning in credit card debt. She was making six figures and had nothing to show for it. Not because she was bad with money. Because nobody had ever shown her how.",
    "There's a story they tell on Wall Street about a janitor who died with eight million dollars in the bank. He wasn't a genius investor. He just did one thing consistently for sixty years.",
    "I met a couple in their mid-thirties who had saved almost nothing for retirement. They were terrified. Six years later, they had a hundred thousand dollars. Not because they got rich. Because they understood one simple concept.",
]

HOOKS = {
    "index_funds": [
        "Warren Buffett made a ten-year bet that a simple index fund would beat a portfolio of elite hedge funds. He won so badly it wasn't even close. The hedge funds charged millions in fees and still lost.",
        "There's a dirty secret on Wall Street that most brokers won't tell you. The vast majority of professional money managers fail to beat the market. Year after year. And they're charging you for the privilege of losing.",
    ],
    "compound_interest": [
        "Albert Einstein reportedly called it the eighth wonder of the world. The math is so powerful that even a small amount of money, given enough time, can grow into a fortune. But most people never let it work.",
        "If you invested a single dollar in the stock market in 1926 and did nothing, it would be worth over ten thousand dollars today. Not through genius stock picks. Through the quiet, relentless power of compounding.",
    ],
    "credit_score": [
        "Your credit score is the most expensive number you don't understand. A thirty-point difference can cost you over fifty thousand dollars in extra interest over your lifetime. And most people don't know what's in their score.",
        "Banks don't want you to know how credit scores actually work. The system is designed to keep you guessing, making small mistakes that cost you thousands in higher rates on everything from car loans to mortgages.",
    ],
    "budgeting": [
        "The word budget sounds like punishment. Like you're supposed to track every penny and feel guilty about buying coffee. That's not budgeting. That's diet culture applied to money, and it doesn't work.",
        "According to a study by the Federal Reserve, nearly forty percent of Americans couldn't cover a four-hundred-dollar emergency with cash. Not a crisis. A car repair. Budgeting isn't about restriction. It's about making sure that repair doesn't wreck your life.",
    ],
    "beginners_investing": [
        "The stock market terrifies most beginners. And Wall Street loves that. Because when you're scared, you either do nothing, or you pay someone else to handle it. Both are expensive mistakes.",
        "You don't need a finance degree. You don't need to watch CNBC every morning. You don't need to pick the next Amazon. Building wealth in the stock market comes down to three things. And none of them require timing the market.",
    ],
    "emergency_fund": [
        "One in three Americans would have to go into debt to cover a thousand-dollar emergency. A thousand dollars. That's not a luxury problem. That's a structural weakness that turns small setbacks into financial disasters.",
        "The single biggest threat to your financial plan isn't a market crash. It's a broken water heater. An unexpected layoff. A medical bill. Without an emergency fund, a minor inconvenience becomes a spiral of high-interest debt.",
    ],
    "retirement_401k": [
        "There's over thirty billion dollars in unclaimed employer 401k matches sitting on the table every year. That's free money that Americans are leaving behind because they never signed up. Or they didn't contribute enough.",
        "Your employer is offering you a raise. But you have to take it. The 401k match is the closest thing to free money you'll ever get in the working world. And roughly a quarter of eligible workers don't take full advantage.",
    ],
    "roth_ira": [
        "Imagine the government created a special account that let you invest money, grow it for decades, and never pay a dime in taxes when you took it out. That exists. It's called a Roth IRA. And most people barely use it.",
        "Tax-free growth sounds too good to be true. But the Roth IRA is real, it's legal, and it's one of the most powerful wealth-building tools available to ordinary Americans. Here's how to make it work for you.",
    ],
    "dollar_cost_averaging": [
        "Timing the market sounds smart. Buy low, sell high. But even the pros get it wrong most of the time. There's a better way, and it's almost boring in its simplicity. It's called dollar-cost averaging.",
        "Legendary investor Peter Lynch once said that far more money has been lost preparing for market corrections than in the corrections themselves. The antidote to timing anxiety is a strategy so simple that most people dismiss it.",
    ],
    "dividends": [
        "There are companies that literally pay you to own their stock. Every quarter, they send you a check just for being a shareholder. It's not a gimmick. Dividend investing is how many of the world's wealthiest families stay wealthy.",
        "Passive income is one of the most overused phrases in personal finance. But dividends are the real thing. Companies sharing their profits with you, without you having to sell a single share.",
    ],
}

TOPIC_CONFIG = {
    "index_funds": {"title": "Index Funds: The Lazy Way to Beat Wall Street", "section1": "WHY ACTIVE MANAGEMENT LOSES", "section2": "THE MATH BEHIND INDEX FUNDS", "section3": "HOW TO BUILD YOUR INDEX PORTFOLIO", "section4": "COMMON INDEX FUND MISTAKES"},
    "compound_interest": {"title": "Compound Interest: The Secret Millionaire Maker", "section1": "WHY TIME MATTERS MORE THAN MONEY", "section2": "THE RULE OF SEVENTY-TWO", "section3": "HOW TO HARNESS COMPOUNDING", "section4": "WHERE COMPOUNDING GOES WRONG"},
    "credit_score": {"title": "Credit Scores: How to Save Thousands Instantly", "section1": "WHAT'S ACTUALLY IN YOUR SCORE", "section2": "HOW BANKS USE YOUR SCORE AGAINST YOU", "section3": "FIVE MOVES TO BOOST YOUR SCORE", "section4": "MYTHS THAT KEEP YOUR SCORE LOW"},
    "budgeting": {"title": "Budgeting That Actually Works (No Spreadsheets)", "section1": "WHY TRADITIONAL BUDGETS FAIL", "section2": "THE SIXTY-TWENTY-TWENTY RULE", "section3": "AUTOMATE YOUR WAY TO FREEDOM", "section4": "THE BIGGEST BUDGET KILLERS"},
    "beginners_investing": {"title": "Investing for Beginners: Start Here in 2026", "section1": "THE THREE PILLARS OF INVESTING", "section2": "UNDERSTANDING RISK VS REWARD", "section3": "YOUR FIRST INVESTMENT PORTFOLIO", "section4": "MISTAKES EVERY BEGINNER MAKES"},
    "emergency_fund": {"title": "Emergency Funds: Your Financial Airbag", "section1": "WHY YOU NEED ONE TODAY", "section2": "HOW MUCH IS ENOUGH", "section3": "WHERE TO KEEP IT", "section4": "WHEN TO ACTUALLY USE IT"},
    "retirement_401k": {"title": "401k Secrets Your Boss Won't Tell You", "section1": "HOW THE 401K REALLY WORKS", "section2": "THE FREE MONEY YOU'RE LEAVING BEHIND", "section3": "TRADITIONAL VS ROTH: THE RIGHT CHOICE", "section4": "401K MISTAKES THAT COST MILLIONS"},
    "roth_ira": {"title": "Roth IRA: The Ultimate Tax Hack", "section1": "WHY TAX-FREE GROWTH MATTERS", "section2": "ROTH VS TRADITIONAL IRA", "section3": "HOW TO MAXIMIZE YOUR ROTH IRA", "section4": "INCOME LIMITS AND BACKDOOR STRATEGIES"},
    "dollar_cost_averaging": {"title": "Dollar Cost Averaging: Timing Made Irrelevant", "section1": "WHY TIMING IS A LOSING GAME", "section2": "HOW DCA TURNS VOLATILITY INTO PROFIT", "section3": "LUMP SUM VS DCA", "section4": "SETTING UP YOUR AUTOMATIC INVESTMENTS"},
    "dividends": {"title": "Dividend Investing: Get Paid to Own Stocks", "section1": "WHAT DIVIDENDS ACTUALLY ARE", "section2": "THE POWER OF DIVIDEND GROWTH", "section3": "BUILDING A DIVIDEND PORTFOLIO", "section4": "DIVIDEND TRAPS AND RISKS"},
}

SECTION_CONTENT = {
    "index_funds": {
        "section1": [
            "Every year, thousands of professional money managers try to beat the stock market. They have Bloomberg terminals. Research teams. Decades of experience. And year after year, most of them fail.",
            "The SPIVA report, published by S&P Global, tracks how active fund managers perform against their benchmarks. Over a fifteen-year period, roughly ninety percent of large-cap fund managers underperformed the S&P 500.",
            "Think about what that means. If you paid someone to pick stocks for you, there's a ninety percent chance you'd have been better off buying a simple index fund and doing nothing.",
            "And those fees you're paying? The average actively managed fund charges around one percent per year. That doesn't sound like much. But over thirty years, that one percent eats up nearly a third of your potential returns.",
            "Index funds flip this whole model upside down. Instead of trying to beat the market, they simply own the market. No stock picking. No timing. Just the collective performance of hundreds or thousands of companies.",
        ],
        "section2": [
            "Here's the math that makes index funds work. The total U.S. stock market has returned roughly ten percent annually on average over the last century. That includes wars, depressions, recessions, and multiple crashes.",
            "An actively managed fund needs to beat that ten percent, minus its fees, just to break even with the index. After fees and trading costs, the average active fund returns somewhere around eight percent.",
            "Over a thirty-year investing horizon, that two percent gap is enormous. Ten thousand dollars compounded at ten percent for thirty years grows to about a hundred and seventy-five thousand. At eight percent, it's just over a hundred thousand.",
            "That's seventy-five thousand dollars you lost by paying someone to lose to the market. And the irony is, you were paying them for the privilege.",
            "Jack Bogle, the founder of Vanguard, built an entire company around this idea. His philosophy was simple: don't look for the needle in the haystack. Just buy the haystack.",
        ],
        "section3": [
            "Building an index fund portfolio doesn't require a brokerage account with a million dollars. You can start today with as little as a hundred dollars, or even less.",
            "The most popular approach is the three-fund portfolio. Total U.S. stock market index. Total international stock market index. Total bond market index. That's it. Three funds that give you exposure to the entire global economy.",
            "For beginners, a target-date fund is even simpler. You pick the year you plan to retire, and the fund automatically adjusts the mix of stocks and bonds as you get older. One fund. One decision.",
            "If you want to be even more aggressive, you can skip the bonds entirely when you're young. A hundred percent stocks, split between U.S. and international indexes. More volatility, but higher expected returns over long time horizons.",
            "The key is automation. Set up automatic contributions from every paycheck. Buy the same funds every month, regardless of what the market is doing. When prices are down, you buy more shares. When prices are up, you buy fewer. Over time, it averages out beautifully.",
        ],
        "section4": [
            "The most common mistake is chasing performance. A fund that crushed it last year is likely to attract a flood of new money. But last year's winners are rarely next year's winners. By the time you hear about a hot fund, the easy money has already been made.",
            "Second mistake: overcomplicating. People add sector funds, thematic ETFs, and leveraged products until their portfolio looks like a hedge fund melting down. Simpler portfolios outperform complex ones, because you're less likely to tinker with them.",
            "Third mistake: selling during a crash. Index funds only work if you stay invested. The worst thing you can do is sell when the market drops. That locks in your losses and guarantees you miss the recovery.",
            "The fourth mistake is ignoring fees entirely. Most index funds charge tiny expense ratios, but some are significantly more expensive than others. A difference of one tenth of a percent adds up over decades.",
        ],
    },
    "compound_interest": {
        "section1": [
            "Compound interest is simple in theory but profound in practice. You earn interest on your original money, and then you earn interest on that interest. Over time, the interest starts earning its own interest. The growth becomes exponential.",
            "The critical variable is time. Not how much you invest, but how long you let it grow. Someone who starts investing at age twenty-five needs to save roughly half as much per month as someone who starts at thirty-five to reach the same goal.",
            "Here's a concrete example. If you invest five hundred dollars a month starting at age twenty-five, earning an average of eight percent, you'll have about one point seven million dollars by age sixty-five.",
            "If you wait until thirty-five to start, you'd need to invest more than eleven hundred dollars a month to reach the same number. Starting ten years later more than doubles the monthly cost.",
            "That's the real cost of waiting. It's not just the money you didn't invest. It's all the compound growth that money would have generated over those extra years. Financial advisors call this opportunity cost. I call it the procrastination penalty.",
        ],
        "section2": [
            "There's a handy mental shortcut called the Rule of Seventy-Two. Divide seventy-two by your annual rate of return, and you get the number of years it takes for your money to double.",
            "At eight percent, your money doubles roughly every nine years. At twelve percent, every six years. At four percent, every eighteen years. The higher the return, the faster the doubling.",
            "Let's see this in action. You invest twenty thousand dollars at age twenty-five. At eight percent, that becomes forty thousand by age thirty-four. Eighty thousand by forty-three. A hundred and sixty thousand by fifty-two. Three hundred and twenty thousand by sixty-one.",
            "Notice what happens in the later years. The growth from age fifty-two to sixty-one is a hundred and sixty thousand dollars. That's more than eight times your original investment, in a single decade.",
            "That hockey-stick curve is the magic of compounding. It's slow and unimpressive for the first two decades. Then it takes off. The problem is, most people give up during the slow part.",
        ],
        "section3": [
            "To harness compounding, you need three things. Time, consistency, and a reasonable rate of return. You can't get more time if you haven't started. But you can start today.",
            "Consistency means investing regularly, not just when you feel good about the market. Automatic monthly contributions are the single most effective habit for building long-term wealth. Set it and forget it.",
            "Rate of return matters, but don't chase it. A diversified portfolio of low-cost index funds has historically returned seven to ten percent annually. That's enough. Doubling every seven to ten years will make you wealthy if you stay the course.",
            "Reinvesting dividends is critical. Don't take the cash payments. Have them automatically buy more shares. This is compounding at work within the compounding. When your dividends buy shares that generate more dividends, the cycle accelerates.",
        ],
        "section4": [
            "The biggest mistake people make with compounding is withdrawing the money early. Every dollar you pull out is a dollar that will never compound again. And the dollars it would have earned are gone too.",
            "High-interest debt is anti-compounding. Credit cards charging twenty-two percent interest double your debt every three years, by the Rule of Seventy-Two. Before you invest, kill the high-interest debt. There is no investment guaranteed to return twenty-two percent.",
            "Another mistake is being too conservative. Money in a savings account earning three percent barely keeps up with inflation. After taxes and inflation, you're probably losing purchasing power. Compounding only works if your money earns more than inflation eats away.",
            "Finally, people underestimate the impact of fees. A one percent fee doesn't just cost you one percent. Over thirty years, it costs you roughly thirty percent of your total returns, because that one percent never gets to compound either.",
        ],
    },
}


def _generate_segments(topic_key: str, duration_minutes: int) -> list[dict]:
    cfg = TOPIC_CONFIG.get(topic_key, TOPIC_CONFIG["beginners_investing"])
    hooks = HOOKS.get(topic_key, HOOKS["index_funds"])
    content = SECTION_CONTENT.get(topic_key, SECTION_CONTENT["index_funds"])

    segments = []
    seg_num = 0

    def add(role, aud, text, summary=""):
        nonlocal seg_num
        seg_num += 1
        segments.append({
            "id": f"MM-{seg_num:03d}",
            "role": role,
            "aud": aud,
            "text": text,
            "summary": summary or text[:60],
        })

    add("TITLE", "NO", cfg["title"], "Title Card")
    add("NARRATOR", "NO", f"[music intro]", "Music Intro")

    anecdote = random.choice(INTRO_ANECDOTES)
    for s in _chunk_sentences(anecdote, 4):
        add("NARRATOR", "YES", s, "Anecdote")

    hook = random.choice(hooks)
    for s in _chunk_sentences(hook, 3):
        add("NARRATOR", "YES", s, "Hook")

    add("NARRATOR", "YES", "Here's everything you need to know. In plain English. No jargon.", "Intro")

    sections = [
        ("section1", cfg["section1"]),
        ("section2", cfg["section2"]),
        ("section3", cfg["section3"]),
        ("section4", cfg["section4"]),
    ]

    for section_key, section_title in sections:
        add("HEADER", "NO", section_title, f"Section: {section_title}")
        section_content = content.get(section_key, [])
        for paragraph in section_content:
            for chunk in _chunk_sentences(paragraph, 3):
                add("NARRATOR", "YES", chunk, chunk[:60])

    add("HEADER", "NO", "WRAPPING IT UP", "Conclusion header")

    conclusion = _generate_conclusion(topic_key)
    for chunk in _chunk_sentences(conclusion, 4):
        add("NARRATOR", "YES", chunk, "Conclusion")

    add("NARRATOR", "NO", "[outro music and subscribe animation]", "Outro CTA")

    return segments


def _chunk_sentences(text: str, max_sentences: int = 4) -> list[str]:
    import re
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks = []
    for i in range(0, len(sentences), max_sentences):
        chunk = sentences[i:i+max_sentences]
        chunks.append(" ".join(chunk))
    return chunks


def _generate_conclusion(topic_key: str) -> str:
    conclusions = {
        "index_funds": "Index funds aren't exciting. They won't make you rich overnight. But they will make you wealthy over time, with almost no effort. That's the point. Financial success isn't about brilliant moves. It's about not making stupid ones. Buy the haystack. Hold it forever. Let compounding do the heavy lifting.",
        "compound_interest": "The single best time to start investing was twenty years ago. The second best time is today. Compound interest rewards the patient and punishes the procrastinator. Start small. Start now. Let time work its magic.",
        "beginners_investing": "Investing isn't complicated. It's emotional. The math is simple. The hard part is not panicking when the market drops and not getting greedy when it soars. If you master those two emotions, you're already ahead of most professionals.",
    }
    return conclusions.get(topic_key, (
        "Here's the bottom line. You don't need to be a financial genius to build wealth. You need discipline, patience, and a simple plan. "
        "Start today. Automate your investments. Ignore the noise. And let time do what it does best. "
        "If you found this helpful, hit subscribe. Money Matrix publishes new videos every week to help you take control of your financial future."
    ))


def generate_script(topic_key: str = "index_funds", duration_minutes: int = 10, subtitle: str = "") -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cfg = TOPIC_CONFIG.get(topic_key, TOPIC_CONFIG["beginners_investing"])

    segments = _generate_segments(topic_key, duration_minutes)

    title = cfg["title"]
    if subtitle:
        title = f"{title}: {subtitle}"

    safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in title)[:50]
    video_id = f"MM-{safe_name[:15]}-{random.randint(100, 999)}"

    lines = []
    lines.append("=== SEGMENT MANIFEST ===")
    lines.append(f"VIDEO_ID: {video_id}")
    lines.append("CONTENT_MODE: EDUCATIONAL")
    lines.append(f"TOTAL_SEGMENTS: {len(segments)}")
    lines.append("MANIFEST_HASH: PENDING")
    lines.append("=== COLUMNS ===")
    lines.append("ID | ROLE | IMG | AUD | DUR | SUMMARY")
    for seg in segments:
        summary = seg["summary"].replace("|", "-").strip()
        lines.append(f"{seg['id']} | {seg['role']} | YES | {seg['aud']} | auto | {summary}")
    lines.append("=== END MANIFEST ===")
    lines.append("")

    for seg in segments:
        lines.append(f"[{seg['id']}]")
        lines.append(seg["text"])
        lines.append("")

    script = "\n".join(lines)

    path = OUTPUT_DIR / f"{safe_name}.txt"
    path.write_text(script, encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    import sys
    topic = sys.argv[1] if len(sys.argv) > 1 else "index_funds"
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    subtitle = sys.argv[3] if len(sys.argv) > 3 else ""
    path = generate_script(topic, duration, subtitle)
    print(f"Script written: {path}")
