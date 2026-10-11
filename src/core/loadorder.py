'''Turns a load order the user arranged by hand into mods.settings priorities'''

from src.core.scriptmerger import MERGED_FOLDER


def moveFolder(order: list[str], folder: str, index: int) -> list[str]:
    '''The order with folder moved to index (counted after it is taken out). Script Merger's merged files stay
    first, as Script Merger needs.'''
    result = [name for name in order if name != folder]
    index = max(0, min(index, len(result)))
    if folder.casefold() != MERGED_FOLDER.casefold():
        merged = next((i for i, name in enumerate(result) if name.casefold() == MERGED_FOLDER.casefold()), None)
        if merged is not None and index <= merged:
            index = merged + 1
    result.insert(index, folder)
    return result


def prioritiesFor(order: list[str], current: dict[str, int | None], moved: str) -> dict[str, int]:
    '''Priorities that make the game load the folders in this order (the first one wins).

    Folders from the top down to the moved one, and down to the last one that already has a priority, are
    numbered 0, 1, 2...; the folders after that keep having none, which the game loads after all numbered ones in
    A to Z order, as they were listed.'''
    last = order.index(moved) if moved in order else -1
    for index, folder in enumerate(order):
        if current.get(folder) is not None:
            last = max(last, index)
    return {folder: index for index, folder in enumerate(order[: last + 1])}
