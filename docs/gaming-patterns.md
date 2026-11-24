# Ways a fix can pass a check while the barrier remains

A fix agent optimises for whatever is measured. Each pattern below makes a check
go green without helping anyone, so each needs a guard. Add new patterns as you
find them, with the guard that catches them.

| Pattern | What it looks like | Guard |
| --- | --- | --- |
| Hiding the element | `aria-hidden="true"`, `role="presentation"`, `display: none` or `visibility: hidden` added to a failing element | Compare the accessibility tree before and after: no node that carried content or an action may disappear |
| Empty names | An accessible name that satisfies the rule but says nothing: "button", "image", "link", "icon", or the file name | Reject names from a deny list and names equal to the role |
| Deleting the problem | The element, its content or its handler is removed or disabled | Diff visible text and interactive elements: nothing may be lost |
| Fixing one state only | Contrast or focus passes in the default state and fails on hover, focus, expanded or error states | Re-run the check in every state the finding's steps can reach |

A fix that trips any guard is rejected, whatever the checks say.
