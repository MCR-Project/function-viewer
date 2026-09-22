# Function Viewer

Turns a codebase into a graph of its functions and the calls between them, and lets a user explore that graph as draggable cards. It exists to make the flow of a codebase visible instead of jumping between files and grepping for callers.

## Language

### The graph

**Graph**:
The result of analyzing a set of source files: their files, their functions, and the call edges between those functions.

**Function**:
A named callable unit found in source, including methods, associated functions and constructors.
_Avoid_: Method (as a separate concept), node

**Call edge**:
A directed link from a caller function to a callee function, with the line where the call happens. Only calls between functions that were actually loaded become edges.
_Avoid_: Link, dependency

**Entry point**:
The function activated automatically when a project loads: the project's top-level `main`, if it has one.
_Avoid_: Start function, root function

**Language**:
What a source file is written in, as shown by its badge, its Explorer icon and its syntax highlighting. A file has exactly one Language.

**Language plugin**:
The support for one programming language, or for a family of closely related ones whose files import each other (TypeScript and JavaScript share one): the piece that knows how to read those languages' functions and their calls. One plugin may read several Languages; each file keeps its own.
_Avoid_: Parser

### Roots

Each language has its own idea of where a project's names start. The terms below are the ones that exist today; a new language that needs its own notion of root adds its term here.

**Picked root**:
The file or folder the user selects to load. Everything is measured relative to its contents.
_Avoid_: Project root, base directory

**Import root**:
For Python, the directory that absolute imports are written relative to. It may sit below the picked root.
_Avoid_: Source root, package root

**Crate root**:
For Rust, the directory a crate's modules are resolved from: that crate's `src/` directory.
_Avoid_: Package root, workspace root

### The canvas

**Function card**:
The draggable card that shows one function's signature, docstring and syntax-highlighted source.
_Avoid_: Node, box, block

**Wire**:
The glowing line drawn between function cards to show a call edge. The edge is the data; the wire is how it looks.
_Avoid_: Connection, arrow

**Active**:
A function the user has switched on; its card is shown in full.
_Avoid_: Enabled, selected

**Ghost**:
A function that is not active but is shown because it directly calls, or is directly called by, an active function.
_Avoid_: Led-to, neighbor, inactive card

**Trace up / Trace down**:
Activating every function that transitively calls a chosen function (up), or that it transitively calls (down).
_Avoid_: Flow up / flow down, follow flow

**Flow mode**:
An auto-arrangement that places function cards left to right by call depth, in call order.
_Avoid_: Call-order mode

**File mode**:
An auto-arrangement that groups function cards into one draggable frame per source file, then places the frames left to right.
_Avoid_: Folder mode

**Explorer**:
The sidebar's nested tree of the loaded files, each with a small language icon.
_Avoid_: File browser, file list

### Appearance

**Theme**:
The overall appearance of the app: either the **Dark theme** or the **Light theme**. The Dark theme is the original look. "Mode" is not used here because Flow mode and File mode already own that word.
_Avoid_: Color mode, dark mode (as the name of the setting), skin

**Theme preference**:
The user's choice of Theme: **System**, **Light** or **Dark**. It is System until the user picks otherwise, and it is remembered between visits.
_Avoid_: Theme setting, theme mode

**System theme**:
The Theme the user's operating system currently asks for. It decides what the app shows only while the Theme preference is System, and the app follows it as it changes. If the system asks for nothing, the Dark theme applies.
_Avoid_: Auto theme, OS theme
