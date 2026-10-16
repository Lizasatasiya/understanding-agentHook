import React from "react";

export function CartRow({ item }) {
  const [quantity, setQuantity] = React.useState(1);

  return (
    <div className="row">
      <span>{item.name}</span>
      <input
        value={quantity}
        onChange={(e) => setQuantity(Number(e.target.value) || 0)}
      />
    </div>
  );
}
