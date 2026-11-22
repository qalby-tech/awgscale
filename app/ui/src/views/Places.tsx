// Локация: Авто, or one of the exits to be kept on.
import { useTexts } from "../i18n";
import { DestinationKind, type Destination, type Exit } from "../types";

interface PlacesProps {
  exits: Exit[];
  pinned: number | null;
  onChoose: (destination: Destination) => void;
  onClose: () => void;
}

export function Places({ exits, pinned, onChoose, onClose }: PlacesProps) {
  const texts = useTexts();
  return (
    <>
      <div className="dim" onClick={onClose} />
      <section className="sheet" aria-label={texts.location}>
        <span className="grip" />
        <div className="row">
          <h2 className="grow">{texts.location}</h2>
          <button type="button" className="close" aria-label={texts.close} onClick={onClose}>
            ×
          </button>
        </div>
        <div className="stack tight">
          <button
            type="button"
            className="option"
            aria-pressed={pinned == null}
            onClick={() => onChoose({ kind: DestinationKind.Auto })}
          >
            <span className="code">A</span>
            <span className="grow">
              <span className="strong">{texts.auto}</span>
            </span>
          </button>
          {exits.map(exit => (
            <button
              key={exit.index}
              type="button"
              className="option"
              aria-pressed={pinned === exit.index}
              disabled={!exit.up}
              onClick={() => onChoose({ kind: DestinationKind.Exit, index: exit.index })}
            >
              <span className="code">{exit.index}</span>
              <span className="grow">
                <span className="strong">{exit.name}</span>
                <span className="muted small">{exit.up ? `${exit.share}%` : texts.exitDown}</span>
              </span>
              <span className="load">
                <span style={{ width: `${exit.share}%` }} />
              </span>
            </button>
          ))}
        </div>
      </section>
    </>
  );
}
