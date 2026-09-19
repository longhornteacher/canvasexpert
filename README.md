# CanvasExpert

A computer science teacher's project to stay sharp and get some work done.

Free. Private. Open source. Uses your secure Canvas "Personal Access Token". Runs entirely on your computer.

Canvas Expert is a local, teacher-controlled cooperation layer between Canvas and the
desktop AI agent you already use. ChatGPT Desktop, Claude Desktop, or another MCP-capable
agent is the primary working surface; Canvas Expert keeps the Canvas token, private data,
local mirror, approval boundaries, and verified writes on your machine. The browser app is
a small control console for setup, readiness, review, recovery, and diagnostics.

## Getting started

Step 1 - Click the green **Code** button near the top of this page, then **Download ZIP** and unzip it.
Step 2 - Put the unzipped folder anywhere in your own files.
Step 3 - Double-click `Open Canvas Expert.bat`. The first run installs Python and everything else it needs into your own user account, no admin needed, then opens the app in your browser. If Windows Package Manager is unavailable, install Python 3.13 or newer from [python.org](https://www.python.org/downloads/) with **Install for me only**, then run the launcher again. If it ever stops starting, double-click `Repair.bat`.

## What it does

**Create** quizzes, assignments, pages, rubrics, and quick gradebook columns with your agent,
then have Canvas Expert validate, preview, and push them to Canvas. The same Forge contracts
also produce local printable outputs. The control console remains available for setup and
review; it is not intended to duplicate the agent's conversation surface.
Set due, unlock, and lock dates, grading category, module placement, shuffle, time limit, attempts, and
access code on the way out. Classic Quizzes and New Quizzes are both supported. Save a printable PDF and
Word version of anything you build.

**Differentiate** the same work four ways (Support, Core, Accelerate, Extend) and push each tier to its own
group. The tier names stay private to you; students see only a neutral tag you choose, or nothing at all.

**Scoring Sessions** let a connected AI agent discover outstanding work across every Current course,
report one compact student-free digest, and wait for your direction. The agent then prepares only the
exact assignment(s) you select; each SAFE pseudonymized packet remains limited to one assignment.
Canvas's rubric takes precedence; otherwise, the agent asks for bounded scoring guidance. When a
scoring decision is needed, it pauses before submitting validated results through guarded write
handling. Canvas Live is the only place to review or edit posted work; there is no local scoring queue
or hosted grader.

**Gradebook tools** for one course at a time: set Canvas's own late policy, honor per-student extra time,
apply curves, and take snapshots.

**Students** is your class list plus the things Canvas will not hold: accommodations, extra time, small
groups, monitoring flags, and private notes. Student reports pull it together per kid.

**Assessments** imports Eduphoria exports, matches them to your roster, and turns them into a standards
profile, coverage reports, longitudinal history, and grouping suggestions.

**Routines** run the recurring chores so you stop remembering them.

**CanvasAgent** connects the AI you already use. Download one instruction file and paste it into any chat,
or connect Claude Desktop or the ChatGPT desktop app so the assistant can read your course data itself,
pseudonymized, over a local connection that never leaves your machine. The agent can render
assignment and scoring previews in its own app; Canvas Expert supplies the bounded data,
state, safety checks, and actions behind those previews.

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
