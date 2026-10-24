"use client";
import type { JSX } from "react";
import { IconBell } from "@tabler/icons-react";
export default function MarketNotificationsDialog(): JSX.Element { return (<div className="divide-border divide-y p-2">
                                {[
                                    "云启算力达到 7 连板暂定状态",
                                    "新澜医药 13:27 完成回封",
                                    "当前范围出现 4 只炸板未回封",
                                ].map((item, index) => (
                                    <div key={item} className="flex gap-3 px-3 py-4">
                                        <IconBell
                                            className={index ? "text-muted-foreground" : "text-primary"}
                                            aria-hidden="true"
                                        />
                                        <div>
                                            <p className="text-sm">{item}</p>
                                            <small className="text-muted-foreground">
                                                仅页面内演示提醒 · 不发送系统通知
                                            </small>
                                        </div>
                                    </div>
                                ))}
                            </div>); }
