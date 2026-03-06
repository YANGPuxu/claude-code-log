# Changelog

All notable changes to claude-code-log will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [1.1.0] - 2026-03-06

### Changed

- **Fix WebSearch and WebFetch rendering in agent transcripts (#98)**
- **Fix fold-bar colors and System Hook alignment (#89)**
- **Add WebFetch tool renderer (#87)**
- **Merge pull request #83 from daaain/dev/websearch-tool-renderer**
- **Update some outdated docs + VS Code insists on these settings (#86)**
- **Fix double tab opening when clicking links in TUI MarkdownViewer**
- **Simplify WebSearch parser and improve rendering**
- **Use structured toolUseResult for WebSearch parsing**
- **Add analysis content support to WebSearch output**
- **Add documentation for implementing tool renderers**
- **Add WebSearch HTML and Markdown formatters**
- **Add WebSearch tool models and factory parser**
- **Fix snapshot + make sure snapshot order is stable**
- **Improve CSS layout to be responsive for mobile small screens (#77)**
- **Update pyright to 1.1.408 (#82)**
- **Support subagents directory structure (Claude Code 2.1.2+) (#80)**


## [1.0.0] - 2026-01-22

BREAKING CHANGE: cache is now using a SQLite database instead of JSON files!

This shouldn't change how the library works for you, but if you were using it in a custom way, some edge cases might break your setup.

### Changed

- **SQLite cache (#59)**
- **Integrate review feedback from #71 + MarkdownPreview in TUI (#75)**
- **Consolidate Rendering Architecture (#74)**
- **Add Markdown renderer (#71)**
- **HTML polish: tool titles, AskUserQuestion, fold bar, thinking borders (#70)**
- **Rename *Content to *Message and add ToolOutput/ToolUseMessage types (#69)**
- **Remove MessageModifiers (#68)**
- **Refactor content formatting to use dispatcher pattern (#67)**
- **Improve message styling consistency (#66)**
- **Fix user text message deduplication to keep best version (#65)**
- **Integrate coderabbit review suggestions for #63**
- **Remove content_html field from TemplateMessage (#63)**


## [0.9.0] - 2025-12-08

### Changed

- **Polish User Messages (#60)**
- **Extract user preferences from project's .vscode/settings.json. (#61)**
- **Filter out warmup messages + parse IDE tags for concise display in summaries (#57)**
- **Fix cross-session tool pairing on session resume (#56)**
- **Fix Parallel Sidechain Rendering (#54)**
- **CSS Styles Cleanup (#53)**
- **Fix test + lint issues (#55)**
- **Review and polish (0.8dev) (#51)**
- **Integration tests (#52)**
- **More Collapsible Content & Slash Command Support (#50)**
- **Support for Steering Messages and Sidechain Cleanup (#49)**
- **Fix Pygments Lexer Performance Bottleneck (#48)**
- **Foldable messages (#42)**
- **Add more Python versions to testing matrix + fixes for 3.14 (#40)**
- **Handle (but don't render) "queue-operation" + remove GH Pages workflow**
- **Update README link + faster rsync**


## [0.8.0] - 2025-11-08

### Changed

- **Regenerate HTML files + couple tiny changes**
- **Use Pygments to render files and code snippets (#39)**
- **Fix Unicode escape in tool use content rendering (#38)**
- **Introduce visual structure for the conversation and some specialized tool rendering (#37)**


## [0.7.0] - 2025-10-22

### Changed

- **Regenerate JSON + HTML with all the latest merged features**
- **Add image rendering support to tool result content (#32)**
- **Add query parameter support for message type filtering (#34)**
- **feat(search): add search functionality (#31)**


## [0.6.0] - 2025-10-22

### Changed

- **Fix tests on windows (#33)**
- **Remove broken Claude PR review**
- **Convert timestamps to user's local timezone in the browser (#29)**


## [0.5.1] - 2025-10-04

### Changed

- **Wire up JSONL ensure_fresh_cache with converter to ensure HTML updated on change (#27)**


## [0.5.0] - 2025-09-03

### Changed

- **Config + regenerate outputs**
- **Apply ANSI colour parsing to Claude's Bash tool call outputs + strip escape sequences for cursor movement and screen manipulation**
- **Render system and bash commands (#19)**
- **Prevent UnicodeEncodeError: surrogates not allowed – fixes #16**
- **Fix timezone-dependent test failures in template data tests (#18)**
- **Add official Claude Code GitHub Workflow [skip-review] (#15)**


## [0.4.4] - 2025-07-30

### Changed

- **Fix TUI project matching (#11)**
- **Update README.md with TUI demo**


## [0.4.3] - 2025-07-20

### Changed

- **Make it possible to get to project selector in TUI even if pwd is a project + Github releases + fixes (#8)**


## [0.4.2] - 2025-07-18

### Changed

- **Untangle spaghetti with cache and generation race conditions, so now index page is rendering correctly**
- **Reuse session first message preview creation to prevent inconsistency**
- **Add one hour after default timeline view to centre messages and make sure they aren't cut off in the right**


## [0.4.1] - 2025-07-17

### Changed

- **Fix TUI test**
- **Add expanded session info panel to TUI + clean up after TUI exit + fix project name regression + take 1000 instead of 500 chars of first user message**
- **Merge pull request #7 from bbatsell/patch-1**
- **Add `packaging` to main dependencies**
- **Silence cache fill output lines when launching TUI + run test suites individually to fix CI**


## [0.4.0] - 2025-07-16

### Changed

- **Implement TUI to open individual HTML pages for sessions and to resume them with CC**
- **Implement better path handling by reading cwd from messages + link to combined transcript from individual session pages + HTML versioning and command to clear them**
- **Add cache version compatibility checker to prevent it from invalidating after compatible version bumps**


## [0.3.4] - 2025-07-13

### Changed

- **Implement caching (writes processed JSON files into .claude project directories)**
- **Extend ToolUseResult to handle List[ContentItem] to support MCP tool results**
- **Power to Claude**
- **Add Claude Code OAuth workflows**


## [0.3.3] - 2025-07-05

### Changed

- **Hide groups in the timeline instead of items + bug fixes**
- **Get tooltip config working + improve rendering and styling**


## [0.3.2] - 2025-07-03

### Changed

- **Fix initial message lookup for session boxes + only show one hour of timeline to decrease initialisation time**
- **Fix lint issue**
- **Fix sidechain issues in timeline and add to filters + add Playwright browser testing**
- **Docs update**
- **Use Anthropic Python SDK for parsing types + handle sub-assistant and system messages**
- **Fix broken test + add ty and fix type errors**


## [0.3.1] - 2025-07-01

### Changed

- **Timeline tooltips + dead code cleanup**


## [0.3.0] - 2025-06-29

### Changed

- **Add timeline functionality**
- **Rewrite session starter prompt picking script and reuse between pages**
- **Pull out CSS to composable modules + add session list to index page + docs update**


## [0.2.9] - 2025-06-24

### Added

- **Individual Session Files**: Generate separate HTML files for each session with navigation links
- **Cross-Session Summary Matching**: Fixed async summary generation by properly matching summaries from later sessions to their original sessions
- **Session Navigation on Index Page**: Added expandable session lists with summaries and direct links to individual session files

### Fixed

- **Session Summary Display**: Session summaries now appear correctly on both index and transcript pages
- **Session Ordering**: Sessions now appear in ascending chronological order (oldest first) on index page to match transcript page
- **Type Safety**: Improved type checking consistency between index and transcript page processing

## [0.2.8] - 2025-06-23

### Added

- **Runtime Message Filtering**: JavaScript-powered filtering toolbar to show/hide message types
  - Toggle visibility for user, assistant, system, tool use, tool results, thinking, and image messages
  - Live message counts for each type
  - Select All/None quick actions
  - Floating filter button for easy access

### Changed

- **Enhanced UI Controls**: Added floating action buttons for better navigation
  - Filter messages button with collapsible toolbar
  - Toggle all details button for expanding/collapsing content
  - Improved back-to-top button positioning


## [0.2.7] - 2025-06-21

### Changed

- **Unwrap messages to not have double boxes**


## [0.2.6] - 2025-06-20

### Changed

- **Token usage stats and usage time intervals on top level index page + make time consistently UTC**
- **Fix example transcript link + exclude dirs from package**


## [0.2.5] - 2025-06-18

### Changed

- **Tiny Justfile fixes**
- **Create docs.yml**
- **Improve expandable details handling + open/close all button + just render short ones + add example**
- **Remove unnecessary line in error message**
- **Script release process**

## [0.2.4] - 2025-06-18

### Changed

- **More error handling**: Add better error reporting with line numbers and render fallbacks

## [0.2.3] - 2025-06-16

### Changed

- **Error handling**: Add more detailed error handling

## [0.2.2] - 2025-06-16

### Changed

- **Static Markdown**: Render Markdown in Python to make it easier to test and not require Javascipt
- **Visual Design**: Make it nicer to look at

## [0.2.1] - 2025-06-15

### Added

- **Table of Contents & Session Navigation**: Added comprehensive session navigation system
  - Interactive table of contents with session summaries and quick navigation
  - Timestamp ranges showing first-to-last timestamp for each session
  - Session-based organization with clickable navigation links
  - Floating "back to top" button for easy navigation

- **Token Usage Tracking**: Complete token consumption display and tracking
  - Individual assistant messages show token usage in headers
  - Session-level token aggregation in table of contents
  - Detailed breakdown: Input, Output, Cache Creation, Cache Read tokens
  - Data extracted from AssistantMessage.usage field in JSONL files

- **Enhanced Content Support**: Expanded message type and content handling
  - **Tool Use Rendering**: Proper display of tool invocations and results
  - **Thinking Content**: Support for Claude's internal thinking processes
  - **Image Handling**: Display of pasted images in transcript conversations
  - **Todo List Rendering**: Support for structured todo lists in messages

- **Project Hierarchy Processing**: Complete project management system
  - Process entire `~/.claude/projects/` directory by default
  - Master index page with project cards and statistics
  - Linked navigation between index and individual project pages
  - Project statistics including file counts and recent activity

- **Improved User Experience**: Enhanced interface and navigation
  - Chronological ordering of all messages across sessions
  - Session demarcation with clear visual separators
  - Always-visible scroll-to-top button
  - Space-efficient, content-dense layout design

### Changed

- **Default Behavior**: Changed default mode to process all projects instead of requiring explicit input
  - `claude-code-log` now processes `~/.claude/projects/` by default
  - Added `--all-projects` flag for explicit project processing
  - Maintained backward compatibility for single file/directory processing

- **Output Structure**: Restructured HTML output for better organization
  - Session-based navigation replaces simple chronological listing
  - Enhanced template system with comprehensive session metadata
  - Improved visual hierarchy with table of contents integration

- **Data Models**: Expanded Pydantic models for richer data representation
  - Enhanced TranscriptEntry with proper content type handling
  - Added UsageInfo model for token usage tracking
  - Improved ContentItem unions for diverse content types

### Technical

- **Template System**: Major improvements to Jinja2 template architecture
  - New session navigation template components
  - Token usage display templates
  - Enhanced message rendering with rich content support
  - Responsive design improvements

- **Testing Infrastructure**: Comprehensive test coverage expansion
  - Increased test coverage to 78%+ across all modules
  - Added visual style guide generation
  - Representative test data based on real transcript files
  - Extensive test documentation in test/README.md

- **Code Quality**: Significant refactoring and quality improvements
  - Complete Pydantic migration with proper error handling
  - Improved type hints and function documentation
  - Enhanced CLI interface with better argument parsing
  - Comprehensive linting and formatting standards

### Fixed

- **Data Processing**: Improved robustness of transcript processing
  - Better handling of malformed or incomplete JSONL entries
  - More reliable session detection and grouping
  - Enhanced error handling for edge cases in data parsing
  - Fixed HTML escaping issues in message content

- **Template Rendering**: Resolved template and rendering issues
  - Fixed session summary attachment logic
  - Improved timestamp handling and formatting
  - Better handling of mixed content types in templates
  - Resolved CSS and styling inconsistencies

## [0.1.0]

### Added

- **Summary Message Support**: Added support for `summary` type messages in JSONL transcripts
  - Summary messages are displayed with green styling and "Summary:" prefix
  - Includes special CSS class `.summary` for custom styling
  
- **System Command Visibility**: System commands (like `init`) are now shown instead of being filtered out
  - Commands appear in expandable `<details>` elements
  - Shows command name in the summary (e.g., "Command: init")
  - Full command content is revealed when expanded
  - Uses orange styling with `.system` CSS class
  
- **Markdown Rendering Support**: Automatic client-side markdown rendering
  - Uses marked.js ESM module loaded from CDN
  - Supports GitHub Flavored Markdown (GFM)
  - Renders headers, emphasis, code blocks, lists, links, and images
  - Preserves existing HTML content when present
  
- **Enhanced CSS Styling**: New styles for better visual organization
  - Added styles for `.summary` messages (green theme)
  - Added styles for `.system` messages (orange theme)  
  - Added styles for `<details>` elements with proper spacing and cursor behavior
  - Improved overall visual hierarchy

### Changed

- **System Message Filtering**: Modified system message handling logic
  - System messages with `<command-name>` tags are no longer filtered out
  - Added `extract_command_name()` function to parse command names
  - Updated `is_system_message()` function to handle command messages differently
  - Other system messages (stdout, caveats) are still filtered as before

- **Message Type Support**: Extended message type handling in `load_transcript()`
  - Now accepts `"summary"` type in addition to `"user"` and `"assistant"`
  - Updated message processing logic to handle different content structures

### Technical

- **Dependencies**: No new Python dependencies added
  - marked.js is loaded via CDN for client-side rendering
  - Maintains existing minimal dependency approach
  
- **Testing**: Added comprehensive test coverage
  - New test file `test_new_features.py` with tests for:
    - Summary message type support
    - System command message handling  
    - Markdown script inclusion
    - System message filtering behavior
  - Tests use anonymized fixtures based on real transcript data

- **Code Quality**: Improved type hints and function documentation
  - Added proper docstrings for new functions
  - Enhanced error handling for edge cases
  - Maintained backward compatibility with existing functionality

### Fixed

- **Message Processing**: Improved robustness of message content extraction
  - Better handling of mixed content types in transcript files
  - More reliable text extraction from complex message structures

## Previous Versions

Earlier versions focused on basic JSONL to HTML conversion with session demarcation and date filtering capabilities.
