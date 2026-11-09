# 0011: Preserve the studio design while improving interaction accessibility

Status: Accepted

The studio already has a token-based visual and motion system. Improve its existing controls
without introducing a second design system or new dependencies.

Wait for server settings before allowing generation, and expose settings loading and retry
states using the existing error component. Disable specification edits and provider changes
while submitting so the visible values match the request. Describe the minimum length before
typing and expose invalid short specifications to assistive technology.

Follow activity updates only while the reader is near the bottom (within 24px); scrolling up
preserves their reading position. Returning to the bottom resumes following automatically.
Keep the existing entry animations and reduced-motion behavior.

Wrap long server errors within their container. Give the endpoint table column headers and a
named keyboard-focusable scroll region. Announce validation severity explicitly instead of
relying on colored decorative icons. Reuse existing colors, spacing, and components throughout.
Give generated file and folder controls a 44px minimum height for coarse pointers.
