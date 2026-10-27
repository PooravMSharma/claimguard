interface Props {
  title: string;
  note: string;
}

export default function Placeholder({ title, note }: Props) {
  return (
    <section>
      <h1 className="page-title">{title}</h1>
      <div className="card muted">{note}</div>
    </section>
  );
}
