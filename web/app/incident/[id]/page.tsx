import { IncidentDetail } from "@/components/IncidentDetail";

export default async function IncidentPage({
  params
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <IncidentDetail incidentId={id} />;
}
