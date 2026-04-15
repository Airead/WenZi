# Emoji Search

Search and paste emoji via the WenZi launcher.

## Usage

Type `e ` followed by a keyword in the launcher:

- `e kai xin` → 😀
- `e cat` → 🐱
- `e huo che` → 🚄

Press **Enter** to paste the selected emoji into the active application.  
Press **Cmd+Enter** to copy it to the clipboard.

### Browse by group

Type `e @` to browse all emoji categories. Each row shows the group name with its representative emoji on the left, and the preview panel on the right displays **all emoji in that group**.

Press **Tab** on a selected group to auto-complete the search box to `e @groupname `, then continue typing a keyword to search within that group.

### Search by group

Use `@groupname` to restrict the search to a specific category or subcategory. The group name supports fuzzy matching, so you do not need to type it exactly.

- `e @dongwu` → all emoji in the "Animals & Nature" group
- `e @face eye` → search for "eye" only within the "face-*" subgroups
- `e mao @动物` → search for "mao" within groups matching "动物"

If the group filter does not match anything, the search automatically falls back to the global pool.

### How group filters work

The plugin parses `@groupname` from the query by consuming words after `@` from the beginning until a known group or subgroup is matched. This allows multi-word group names without requiring quotes.

## Data source

Emoji data is adapted from [angelofan/emoji-json](https://github.com/angelofan/emoji-json), which provides Simplified Chinese translations synchronized with the Unicode Consortium releases.
