// SPDX-License-Identifier: AGPL-3.0-only
"use client"

import * as React from "react"
import {
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table"
import { ArrowUpDown } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

/**
 * Creates a sortable header for a column.
 */
export function SortableHeader({ column, children }) {
  return (
    <Button
      variant="ghost"
      className="gap-1 px-0 hover:bg-transparent"
      onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
    >
      {children}
      <ArrowUpDown className="h-3.5 w-3.5 text-muted-foreground/60" />
    </Button>
  )
}

/**
 * Reusable data table built on @tanstack/react-table + shadcn Table.
 *
 * Props:
 * - columns: ColumnDef[]
 * - data: any[]
 * - pageSize?: number (default 10)
 * - emptyMessage?: string
 * - loading?: boolean
 * - loadingRows?: number
 *
 * A column can set `meta: { numeric: true }` to mark its header and cells
 * with data-numeric="true" (styled right-aligned, tabular figures).
 */
export function DataTable({
  columns,
  data,
  pageSize = 10,
  emptyMessage = "No results.",
  loading = false,
  loadingRows = 6,
}) {
  const [sorting, setSorting] = React.useState([])

  const table = useReactTable({
    data,
    columns,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getSortedRowModel: getSortedRowModel(),
    state: { sorting },
    initialState: { pagination: { pageSize } },
  })

  const pageCount = table.getPageCount()

  return (
    <div className="w-full">
      <div className="overflow-hidden">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <TableHead key={header.id} data-numeric={header.column.columnDef.meta?.numeric ? "true" : undefined}>
                    {header.isPlaceholder
                      ? null
                      : flexRender(header.column.columnDef.header, header.getContext())}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: loadingRows }).map((_, rowIndex) => (
                <TableRow key={`loading-${rowIndex}`} aria-hidden="true">
                  {columns.map((column, columnIndex) => (
                    <TableCell key={column.id || column.accessorKey || columnIndex} data-numeric={column.meta?.numeric ? "true" : undefined}>
                      <Skeleton className={columnIndex === 0 ? "h-4 w-[min(14rem,85%)]" : "h-4 w-[min(7rem,70%)]"} />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : table.getRowModel().rows?.length ? (
              table.getRowModel().rows.map((row) => (
                <TableRow key={row.id}>
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id} data-numeric={cell.column.columnDef.meta?.numeric ? "true" : undefined}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell colSpan={columns.length} className="h-24 text-center text-muted-foreground">
                  {emptyMessage}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      {!loading && pageCount > 1 && (
        <div className="flex items-center justify-end space-x-2 py-4">
          <div className="flex-1 text-sm text-muted-foreground">
            Page {table.getState().pagination.pageIndex + 1} of {pageCount}
          </div>
          <div className="space-x-2">
            <Button variant="outline" size="sm" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}>
              Previous
            </Button>
            <Button variant="outline" size="sm" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
