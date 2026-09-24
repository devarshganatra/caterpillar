import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { IncidentDetail } from "../components/incidents/IncidentDetail";
import { AppLayout } from "../components/layout/AppLayout";

export function IncidentPage() {
  const { incidentId } = useParams<{ incidentId: string }>();
  const navigate = useNavigate();

  if (!incidentId) return null;

  return (
    <AppLayout>
      <div className="p-8 max-w-4xl mx-auto">
        <button
          onClick={() => navigate(-1)}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors mb-6"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Fleet
        </button>
        <IncidentDetail incidentId={incidentId} />
      </div>
    </AppLayout>
  );
}
