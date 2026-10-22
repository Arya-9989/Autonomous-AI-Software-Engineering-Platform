import { FileQuestion } from "lucide-react";
import { useEffect } from "react";
import { Link } from "react-router";

import { EmptyState } from "../components/ui";
import { pill } from "../lib/pill";

export function NotFound() {
  useEffect(() => {
    document.title = "Page not found · AutoSWE";
  }, []);
  return (
    <EmptyState
      icon={<FileQuestion />}
      title="Page not found"
      heading="h1"
      action={
        <Link to="/app" className={pill("primary", "sm")}>
          Start a new project
        </Link>
      }
    >
      The address may be mistyped, or the page has moved.
    </EmptyState>
  );
}
