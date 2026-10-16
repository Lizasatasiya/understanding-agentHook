import React from "react";

export function CartRow({ item }) {
  const [quantity, setQuantity] = React.useState(1);

  const handleAdd = () => {
    setQuantity((q) => q + 1);
    item.onAdd && item.onAdd(item.id, quantity);
  };

  return (
    <div className="row">
      <span>{item.name}</span>
      <input
        value={quantity}
        onChange={(e) => setQuantity(Number(e.target.value) || 0)}
      />
      <button onClick={handleAdd}>Add</button>
    </div>
  );
}
