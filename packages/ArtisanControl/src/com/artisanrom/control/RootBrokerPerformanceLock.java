// SPDX-License-Identifier: GPL-3.0-or-later
package com.artisanrom.control;

import java.nio.file.Files;
import java.nio.file.Path;

/** Exclusive lock shared with the vendor boot-profile shell service. */
public final class RootBrokerPerformanceLock implements AutoCloseable {
    private final Path path;
    private RootBrokerPerformanceLock(Path path){this.path=path;}
    public static RootBrokerPerformanceLock acquire(Path path)throws Exception{
        Files.createDirectory(path);
        return new RootBrokerPerformanceLock(path);
    }
    public void close()throws Exception{Files.delete(path);}
}
