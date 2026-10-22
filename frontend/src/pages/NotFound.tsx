import { FileQuestion } from "lucide-react";
import { Link } from "react-router";

import { EmptyState } from "../components/ui";

export function NotFound() {
  return (
    <EmptyState
      icon={<FileQuestion className="size-6" />}
      title="Page not found"
      action={
        <Link to="/" className="text-sm font-medium text-brand-600 hover:underline dark:text-brand-400">
          Back to start
        </Link>
      }
    />
  );
}
