import { Compass } from "lucide-react";
import { Link } from "react-router-dom";

import { Card, EmptyState } from "../components/ui";

export default function NotFound() {
  return (
    <Card>
      <EmptyState
        icon={Compass}
        title="This screen does not exist"
        body="The link may be out of date."
        action={
          <Link to="/" className="btn-primary mt-1">
            Back to the dashboard
          </Link>
        }
      />
    </Card>
  );
}
