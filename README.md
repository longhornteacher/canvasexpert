# CanvasExpert

A computer science teacher's project to stay sharp and get some work done.

Free and open source. It uses your Canvas "Personal Access Token" and runs on your own
computer; the token and the map of real names to stand-in names stay there.

Canvas Expert sits between Canvas and the desktop AI agent you already use, such as Claude
Desktop or the ChatGPT desktop app. You work in conversation with your agent. Canvas Expert
holds the Canvas token and your private data, keeps a local copy of your courses, shows the
agent student work only under stand-in names, and makes every Canvas change go through a
preview you approve, a check after it lands, and a receipt. A small browser console handles
setup, connections, recovery, receipts and private name lookups.

## Getting started

1. Click the green **Code** button near the top of this page, then **Download ZIP**, and unzip it
   anywhere in your own files.
2. Double-click `Open Canvas Expert.bat`. The first run installs Python and everything else it
   needs into your own user account, with no admin rights, then opens the console in your
   browser. If Windows Package Manager is unavailable, install Python 3.13 or 3.14 for Windows x64 from
   [python.org](https://www.python.org/downloads/) with **Install for me only**, then run the
   launcher again. If it ever stops starting, double-click `Repair.bat`.
3. Follow the setup steps, then use **Connect** on the CanvasAgent page for Claude Desktop or
   ChatGPT. Restart that app and ask it to list your courses.

Using more than one computer? Point each one at the same OneDrive workspace; see
[docs/guides/more-than-one-computer.md](docs/guides/more-than-one-computer.md).

## What it does

**Create.** Your agent drafts quizzes (Classic or New Quizzes), assignments and pages. Canvas
Expert validates them, shows a preview, and pushes them after you say go, with due, unlock and
lock dates, assignment group, module placement and publish state.

**Differentiate.** The same work in three tiers (Support, Core, Accelerate), each with a public
Canvas tag you choose. Canvas Expert keeps no student-to-tier mapping; you assign tiers to
students in Canvas.

**Score and give feedback.** Your agent finds outstanding work across your current courses,
reports what it found, and waits for you to choose. For each assignment you pick, it reads the
work under stand-in names, follows the Canvas rubric (or asks you for scoring guidance),
stages scores and comments, and shows you a preview with any warnings before anything posts.
Canvas Expert posts what you approve and checks it landed. Notes the agent writes for you,
such as integrity concerns, stay with you and never reach Canvas. You can also reopen graded
work to revise the feedback only.

**Gradebook.** Grade adjustments and curve rules, extra attempts, gradebook snapshots, and SIS
grade bridges for differentiated work, each previewed before it changes anything. Late work is
counted in days from the student's first real attempt; Canvas applies its own late penalty.

**Roster.** Your agent manages extra time, monitored students and classroom profiles through
stand-in names. The **Names** page in the console is where you look up who is who.

Everything reads from a local copy of your Canvas data, so it stays fast. Your agent refreshes
that copy when it's out of date, and Canvas Expert updates itself when you say so.

## Learn more

Want more detail? Give `api/default_docs/AI Authoring/START HERE - CanvasAgent.txt` to a
chatbot and ask it anything. That file is also the CanvasAgent instruction set: paste the
whole thing into an AI chat, or just its CORE block into a custom-instructions box, and the
assistant knows how to draft coursework Canvas Expert can validate and push.

For developers: see `AGENTS.md` and `docs/README.md`. To continue the current
`dev` pilot from another computer, use
[the Git checkout guide](docs/guides/continue-dev-on-another-computer.md).

## License

MIT, see `LICENSE`.

## AI Disclosures

CanvasExpert was built by a teacher working with AI coding tools.

Nothing goes to an AI service until you connect one. When your agent reads from Canvas Expert,
real names and Canvas/SIS ID numbers are replaced with stable stand-in names, and the result is
checked again before it is returned. The map from real names to stand-ins stays on your
computer and is never sent.

That is not a guarantee. The check knows the students on your synced rosters and nothing else,
so a name it has never seen, a name spelled differently than Canvas spells it, or a phone
number, address, or email a student typed into their own work can pass through. Stand-in names
make student data pseudonymized, not anonymous. Before anything goes back to Canvas, your agent
shows you a preview; read it.

Never send anyone's personally identifiable information (PII) to an AI service. Not only is
that uncool, it's illegal.

If you use this, follow all relevant employer policies and rules.
