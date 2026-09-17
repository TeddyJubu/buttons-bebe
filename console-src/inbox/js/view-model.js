export const views = [
  { id: "open", label: "Open" },
  { id: "mine", label: "Assigned to me" },
  { id: "unassigned", label: "Unassigned" },
  { id: "all", label: "All" },
  { id: "snoozed", label: "Snoozed" },
  { id: "closed", label: "Closed" },
];

// Single source for the view-id set (report 10, action 7): the menu above,
// the WebMCP enum, and the helpdesk VIEWS tuple must list the same ids.
export const VIEW_IDS = Object.freeze(views.map((view) => view.id));


export function ticketInView(ticket, viewId) {
  if (viewId === "all") return true;
  if (viewId === "open") return ticket.status === "open";
  if (viewId === "mine") return ticket.assignee === "me" && ticket.status === "open";
  if (viewId === "unassigned") return ticket.assignee == null && ticket.status === "open";
  if (viewId === "snoozed") return ticket.status === "snoozed";
  if (viewId === "closed") return ticket.status === "closed";
  return false;
}

export function viewCounts(list = []) {
  return Object.fromEntries(views.map((view) => [
    view.id,
    list.filter((ticket) => ticketInView(ticket, view.id)).length,
  ]));
}
