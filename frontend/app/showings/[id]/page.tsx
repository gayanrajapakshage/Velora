import SeatMapClient from "./SeatMapClient";

export default async function ShowingPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <SeatMapClient showingId={id} />;
}
