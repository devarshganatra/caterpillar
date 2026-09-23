import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { IncidentDetail } from "../components/incidents/IncidentDetail";

export function IncidentPage() {
  const { incidentId } = useParams<{ incidentId: string }>();
  const navigate = useNavigate();

  if (!incidentId) return null;

  return (
    <div className="min-h-screen p-8 max-w-4xl mx-auto space-y-6">
      <button
        onClick={() => navigate(-1)}
        className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
      >
        <ArrowLeft className="w-4 h-4" /> Back
      </button>
      <IncidentDetail incidentId={incidentId} />
    </div>
  );
}
