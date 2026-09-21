# Function Viewer

Turns a codebase into a graph of its functions and the calls between them, and lets a user explore that graph as draggable cards. It exists to make the flow of a codebase visible instead of jumping between files and grepping for callers.

## Language

### The graph

**Graph**:
The result of analysing a set of source files: their files, their functions, and the call edges between those functions.

**Function**:
A named callable unit found in source, including methods, associated functions and constructors.
_Avoid_: Method (as a separate concept), node

**Call edge**:
A directed link from a caller function to a callee function, with the line where the call happens. Only calls between functions that were actually loaded become edges.
_Avoid_: Link, dependency

**Entry point**:
The function activated automatically when a project loads: the shallowest top-level `main`.
_Avoid_: Start function, root function

**Language plugin**:
The part of the system that understands one programming language: it finds that language's functions and resolves which loaded functions each one calls.
_Avoid_: Parser, analyzer (the analyzer is the whole multi-language dispatcher)

### Roots

Each language has its own idea of where a project's names start, and the analyser infers it per language. The terms below are the ones that exist today; a new language that needs its own notion of root adds its term here.

**Picked root**:
The file or folder the user selects to load. Everything is measured relative to its contents.
_Avoid_: Project root, base directory

**Import root**:
For Python, the directory that absolute imports are written relative to, inferred from the imports the project actually uses. It may sit below the picked root.
_Avoid_: Source root, package root

**Crate root**:
For Rust, the directory a crate's modules are resolved from: the nearest `src/` ancestor of a file.
_Avoid_: Package root, workspace root

### The canvas

**Function card**:
The draggable card that shows one function's signature, docstring and syntax-highlighted source.
_Avoid_: Node, box, block

**Wire**:
The glowing line drawn between function cards to show a call edge. The edge is the data; the wire is how it looks.
_Avoid_: Connection, arrow

**Active**:
A function the user has switched on. Its card is shown in full and it drives what else appears.
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
