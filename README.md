# CanvasExpert

A computer science teacher's project to stay sharp and get some work done.

Free. Private. Open source. Uses your secure Canvas "Personal Access Token". Runs entirely on your computer.

## Getting started

Step 1 - Click the green **Code** button near the top of this page, then **Download ZIP** and unzip it.
Step 2 - Put the unzipped folder anywhere in your own files.
Step 3 - Double-click `Open Canvas Expert.bat`. The first run installs Python and everything else it needs into your own user account, no admin needed, then opens the app in your browser. If Windows Package Manager is unavailable, install Python 3.13 or newer from [python.org](https://www.python.org/downloads/) with **Install for me only**, then run the launcher again. If it ever stops starting, double-click `Repair.bat`.

## What it does

**Create** quizzes, assignments, pages, rubrics, and quick gradebook columns, then push them to Canvas.
Draft in the app or bring a draft back from an AI chat, validate it, dry-run the push, then send it live.
Set due, unlock, and lock dates, grading category, module placement, shuffle, time limit, attempts, and
access code on the way out. Classic Quizzes and New Quizzes are both supported. Save a printable PDF and
Word version of anything you build.

**Differentiate** the same work four ways (Support, Core, Accelerate, Extend) and push each tier to its own
group. The tier names stay private to you; students see only a neutral tag you choose, or nothing at all.

**Scoring Sessions** let a connected AI agent work through one frozen Current-course scoring backlog
without starting a new session per assignment. Each SAFE pseudonymized packet remains limited to one
assignment. Canvas's rubric takes precedence; otherwise, the agent asks you to select a CanvasExpert
rubric or provide scoring guidance. When missing-score policy or another scoring decision is needed,
the agent pauses before submitting validated results through guarded write handling, then continues the
same session. Canvas Live is the only place to review or edit posted work; there is no local scoring
queue or hosted grader.

**Gradebook tools** for one course at a time: set Canvas's own late policy, sweep late work by counting real
school days instead of calendar days, honor per-student extra time, grant extensions, apply curves, and take
snapshots.

**Students** is your class list plus the things Canvas will not hold: accommodations, extra time, small
groups, monitoring flags, and private notes. Student reports pull it together per kid.

**Assessments** imports Eduphoria exports, matches them to your roster, and turns them into a standards
profile, coverage reports, longitudinal history, and grouping suggestions.

**Calendar** holds your school year, bell schedules, no-school days, public events, and your own teaching
schedule in one place. The late-work sweep and your routines both read from it.

**Routines** run the recurring chores so you stop remembering them.

**CanvasAgent** connects the AI you already use. Download one instruction file and paste it into any chat,
or connect Claude Desktop or the ChatGPT desktop app so the assistant can read your course data itself,
pseudonymized, over a local connection that never leaves your machine.

Everything works from a local copy of your Canvas data, so the app stays fast and keeps working when the
network does not. It refreshes in the background and updates itself when you say so.

## Learn more

Want more detail? Give `api/default_docs/AI Authoring/START HERE - CanvasAgent.txt` to a
chatbot and ask it anything. That file is also the CanvasAgent instruction set: paste the
whole thing into an AI chat, or just its CORE block into a custom-instructions box, and the
assistant knows how to draft coursework CanvasExpert can validate and push.

For developers: see `AGENTS.md` and `docs/README.md`.

## License

MIT, see `LICENSE`.

## AI Disclosures

CanvasExpert was built by a teacher working with AI coding tools.

The AI features are optional and off until you turn them on. When you use one, CanvasExpert replaces real
names and Canvas/SIS ID numbers with stable fake ones before anything leaves your computer, and checks the
result again before it sends. The real-to-fake map stays on your machine and is never transmitted.

That is not a guarantee. The check knows the students on your synced rosters and nothing else, so a name it
has never seen, a name spelled differently than Canvas spells it, or a phone number, address, or email a
student typed into their own essay can pass through. Read what you are about to send. The app shows it to
you first for exactly that reason.

Never send anyone's personally identifiable information (PII) to an AI service. Not only is that uncool,
it's illegal.

If you use this, follow all relevant employer policies and rules.
