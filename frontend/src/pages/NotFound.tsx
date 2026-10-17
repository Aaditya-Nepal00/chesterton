import { Link } from "react-router";
import { EmptyState, PageTitle } from "../components/ui";

export function NotFound() {
  return (
    <>
      <PageTitle>Not found</PageTitle>
      <EmptyState title="There's no page at this address." action={<Link to="/">Back to the overview</Link>}>
        Runs live at <span className="mono">/runs/&lt;run id&gt;</span>, PR analyses at{" "}
        <span className="mono">/pr/&lt;owner&gt;/&lt;repo&gt;/&lt;number&gt;</span>.
      </EmptyState>
    </>
  );
}
